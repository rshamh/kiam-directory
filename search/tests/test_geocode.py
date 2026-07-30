"""Geocoding.

Every test here fakes the HTTP layer. The point of the module is what it does when
the other end misbehaves, and you cannot test that against a service that is
working — nor should a test suite depend on somebody else's uptime.

The rule the whole module exists to keep: **a geocoder failure is never a search
failure.** Every path that cannot produce a point returns ``None``, and the search
view treats that as "no location given".
"""

from __future__ import annotations

import json

import pytest
from django.core.cache import cache

from search.services import geocode

pytestmark = pytest.mark.django_db


class FakeResponse:
    def __init__(self, payload=None, status_code=200, raw=None):
        self._payload = payload
        self.status_code = status_code
        self._raw = raw

    def json(self):
        if self._raw is not None:
            return json.loads(self._raw)  # raises ValueError on garbage
        return self._payload


def _respond(monkeypatch, response=None, *, error=None, record=None):
    def fake_get(url, params=None, timeout=None, headers=None):
        if record is not None:
            record.append({"url": url, "params": params, "timeout": timeout})
        if error is not None:
            raise error
        return response

    monkeypatch.setattr(geocode.requests, "get", fake_get)


POSTCODE_OK = {"result": {"postcode": "KT18 5EP", "latitude": 51.3360, "longitude": -0.2674}}


# ---------------------------------------------------------------------------
# Routing: what did they type?
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["KT18 5EP", "kt185ep", "  SW1A 1AA  ", "M1 1AE"])
def test_a_postcode_is_recognised(text):
    assert geocode.looks_like_postcode(text)


@pytest.mark.parametrize("text", ["Epsom", "KT18", "", "Surrey KT"])
def test_a_place_name_is_not_mistaken_for_a_postcode(text):
    assert not geocode.looks_like_postcode(text)


@pytest.mark.parametrize("text", ["KT18", "SW1A", "M1"])
def test_an_outward_code_alone_is_recognised(text):
    assert geocode.looks_like_outcode(text)


def test_a_postcode_resolves_to_a_point(monkeypatch):
    _respond(monkeypatch, FakeResponse(POSTCODE_OK))

    place = geocode.resolve("KT18 5EP")

    assert place.kind == "postcode"
    assert place.lat == pytest.approx(51.3360)
    assert place.lng == pytest.approx(-0.2674)


def test_a_place_name_goes_to_the_places_endpoint(monkeypatch):
    calls = []
    _respond(
        monkeypatch,
        FakeResponse(
            {
                "result": [
                    {
                        "name_1": "Epsom",
                        "county_unitary": "Surrey",
                        "latitude": 51.33,
                        "longitude": -0.26,
                    }
                ]
            }
        ),
        record=calls,
    )

    place = geocode.resolve("Epsom")

    assert place.label == "Epsom, Surrey"
    assert place.kind == "place"
    assert calls[0]["url"].endswith("/places")


def test_a_place_label_names_its_county(monkeypatch):
    """ "Epsom" alone is ambiguous, and the visitor has to be able to tell which
    Epsom the radius is centred on."""
    _respond(
        monkeypatch,
        FakeResponse(
            {
                "result": [
                    {
                        "name_1": "Newport",
                        "county_unitary": "Isle of Wight",
                        "latitude": 50.7,
                        "longitude": -1.29,
                    }
                ]
            }
        ),
    )

    assert geocode.places("Newport")[0].label == "Newport, Isle of Wight"


def test_blank_input_asks_nobody(monkeypatch):
    _respond(monkeypatch, error=AssertionError("should not have been called"))

    assert geocode.resolve("") is None
    assert geocode.resolve("   ") is None
    assert geocode.places("a") == []


# ---------------------------------------------------------------------------
# Degrading: the part that matters
# ---------------------------------------------------------------------------


def test_a_timeout_returns_none_rather_than_raising(monkeypatch):
    _respond(monkeypatch, error=TimeoutError("read timed out"))

    assert geocode.resolve("KT18 5EP") is None


def test_a_connection_error_returns_none(monkeypatch):
    _respond(monkeypatch, error=OSError("name resolution failed"))

    assert geocode.resolve("KT18 5EP") is None
    assert geocode.places("Epsom") == []


def test_a_server_error_returns_none(monkeypatch):
    _respond(monkeypatch, FakeResponse(status_code=503))

    assert geocode.resolve("KT18 5EP") is None


def test_a_body_that_is_not_json_returns_none(monkeypatch):
    """A proxy returning an HTML error page is a real thing that happens."""
    _respond(monkeypatch, FakeResponse(raw="<html>gateway timeout</html>"))

    assert geocode.resolve("KT18 5EP") is None


def test_a_404_is_an_answer_not_an_outage(monkeypatch):
    """postcodes.io answers 404 for "no such postcode". That is a real answer and a
    cacheable one — it is not the service being unavailable."""
    _respond(monkeypatch, FakeResponse(status_code=404))

    assert geocode.resolve("ZZ99 9ZZ") is None


def test_a_result_with_no_coordinates_returns_none(monkeypatch):
    _respond(monkeypatch, FakeResponse({"result": {"postcode": "KT18 5EP"}}))

    assert geocode.resolve("KT18 5EP") is None


def test_the_request_carries_a_timeout_and_identifies_itself(monkeypatch, settings):
    """A third-party call with no timeout is the fastest way to blow the page's
    latency budget, and being identifiable is what gets us told about a problem
    rather than rate-limited for one."""
    calls = []
    _respond(monkeypatch, FakeResponse(POSTCODE_OK), record=calls)

    geocode.resolve("KT18 5EP")

    assert calls[0]["timeout"] == settings.GEOCODE_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_a_resolved_postcode_is_cached(monkeypatch):
    calls = []
    _respond(monkeypatch, FakeResponse(POSTCODE_OK), record=calls)

    geocode.resolve("KT18 5EP")
    geocode.resolve("KT18 5EP")

    assert len(calls) == 1


def test_the_cache_key_ignores_spacing_and_case(monkeypatch):
    calls = []
    _respond(monkeypatch, FakeResponse(POSTCODE_OK), record=calls)

    geocode.resolve("KT18 5EP")
    geocode.resolve("kt185ep")
    geocode.resolve("  Kt18  5ep ")

    assert len(calls) == 1


def test_a_definite_miss_is_cached_so_a_typo_costs_one_call(monkeypatch):
    calls = []
    _respond(monkeypatch, FakeResponse(status_code=404), record=calls)

    geocode.resolve("ZZ99 9ZZ")
    geocode.resolve("ZZ99 9ZZ")

    assert len(calls) == 1


def test_an_outage_is_NOT_cached(monkeypatch):
    """The distinction the module is built around. Caching "we could not ask" would
    turn a thirty-second blip into a month of empty results."""
    calls = []
    _respond(monkeypatch, error=TimeoutError("down"), record=calls)

    geocode.resolve("KT18 5EP")
    geocode.resolve("KT18 5EP")

    assert len(calls) == 2


def test_a_recovered_service_is_used_immediately(monkeypatch):
    _respond(monkeypatch, error=TimeoutError("down"))
    assert geocode.resolve("KT18 5EP") is None

    _respond(monkeypatch, FakeResponse(POSTCODE_OK))
    assert geocode.resolve("KT18 5EP") is not None


def test_place_searches_are_cached(monkeypatch):
    calls = []
    _respond(
        monkeypatch,
        FakeResponse({"result": [{"name_1": "Epsom", "latitude": 51.3, "longitude": -0.26}]}),
        record=calls,
    )

    geocode.places("Epsom")
    geocode.places("epsom")

    assert len(calls) == 1


def test_the_negative_cache_is_shorter_than_the_positive_one(settings):
    """A genuinely new postcode must not be blacklisted for a month."""
    assert settings.GEOCODE_NEGATIVE_CACHE_SECONDS < settings.GEOCODE_CACHE_SECONDS


def test_a_cached_place_round_trips_through_the_cache(monkeypatch):
    """Places are cached as plain dicts, so a bad round trip would raise on the
    second call rather than the first."""
    _respond(
        monkeypatch,
        FakeResponse(
            {
                "result": [
                    {"name_1": "Epsom", "county_unitary": "Surrey", "latitude": 51.3, "longitude": -0.26}
                ]
            }
        ),
    )

    first = geocode.places("Epsom")
    second = geocode.places("Epsom")

    assert first == second
    assert isinstance(second[0], geocode.Place)
    assert second[0].label == "Epsom, Surrey"


def test_an_outcode_resolves(monkeypatch):
    _respond(
        monkeypatch,
        FakeResponse({"result": {"outcode": "KT18", "latitude": 51.33, "longitude": -0.26}}),
    )

    place = geocode.resolve("KT18")

    assert place.kind == "outcode"
    assert place.label == "KT18"


def test_nothing_is_written_to_the_cache_when_the_service_is_unreachable(monkeypatch):
    cache.clear()
    _respond(monkeypatch, error=TimeoutError("down"))

    geocode.resolve("KT18 5EP")

    assert cache.get("geocode:postcode:KT185EP") is None
