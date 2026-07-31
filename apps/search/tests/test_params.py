"""Turning a query string into SearchParams.

Two things here are security-adjacent rather than cosmetic: an arbitrary radius and
an unbounded facet list are both hand-built URLs that make Postgres do a lot of
work, and both are clamped.

The seed tests pin the decision recorded in the module docstring — a date-derived
seed rather than a session, so that running a search sets no cookie.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.http import QueryDict

from apps.directory.services.search import DEFAULT_RADIUS_MILES
from apps.search.services import params as params_service

pytestmark = pytest.mark.django_db


def _parse(query: str):
    return params_service.parse(QueryDict(query), page_size=20)


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


def test_multi_valued_facets_collect_every_value():
    p = _parse("speciality=a&speciality=b&group=adults&language=en&language=ur")

    assert p.specialities == ["a", "b"]
    assert p.client_groups == ["adults"]
    assert p.languages == ["en", "ur"]


def test_repeated_values_are_deduplicated():
    p = _parse("speciality=a&speciality=a&speciality=a")

    assert p.specialities == ["a"]


def test_a_facet_list_is_capped():
    """A URL with two hundred repeated facet values is a cheap way to make the
    database work hard."""
    query = "&".join(f"speciality=s{i}" for i in range(200))

    assert len(_parse(query).specialities) == 40


def test_blank_and_whitespace_values_are_dropped():
    p = _parse("speciality=&speciality=%20%20&speciality=real")

    assert p.specialities == ["real"]


@pytest.mark.parametrize("raw", ["1", "true", "on", "yes", "TRUE", "On"])
def test_flags_accept_the_usual_truthy_spellings(raw):
    assert _parse(f"verified={raw}").verified_only


@pytest.mark.parametrize("raw", ["0", "false", "off", "", "no", "banana"])
def test_flags_are_false_for_anything_else(raw):
    assert not _parse(f"verified={raw}").verified_only


def test_delivery_maps_to_two_mutually_exclusive_flags():
    assert _parse("delivery=in_person").in_person_only
    assert not _parse("delivery=in_person").online_only

    assert _parse("delivery=online").online_only
    assert not _parse("delivery=online").in_person_only

    either = _parse("delivery=")
    assert not either.in_person_only and not either.online_only


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["500", "0", "-10", "banana", "", "7"])
def test_an_unoffered_radius_falls_back_to_the_default(raw):
    """A 500-mile radius is a table scan dressed up as a search."""
    assert _parse(f"radius={raw}").radius_miles == DEFAULT_RADIUS_MILES


@pytest.mark.parametrize("raw", ["1", "3", "5", "10", "15", "25", "50"])
def test_every_offered_radius_is_accepted(raw):
    assert _parse(f"radius={raw}").radius_miles == int(raw)


@pytest.mark.parametrize("raw", ["0", "-3", "abc", ""])
def test_a_bad_page_number_falls_back_to_one(raw):
    assert _parse(f"page={raw}").page == 1


def test_an_unknown_wait_value_is_ignored():
    assert _parse("wait=eventually").max_wait == ""
    assert _parse("wait=short").max_wait == "short"


def test_a_long_query_is_truncated():
    assert len(_parse(f"q={'x' * 500}").q) == 200


def test_negative_numbers_are_ignored():
    assert _parse("fee_max=-5").fee_max_pence is None
    assert _parse("experience=-1").min_years_experience is None


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------


def test_the_seed_is_stable_for_a_day():
    day = dt.date(2026, 7, 30)

    assert params_service.daily_seed(day) == params_service.daily_seed(day)


def test_the_seed_changes_the_next_day():
    assert params_service.daily_seed(dt.date(2026, 7, 30)) != params_service.daily_seed(dt.date(2026, 7, 31))


def test_an_explicit_seed_wins():
    """So a shared link reproduces exactly the order the sender saw."""
    assert _parse("seed=4242").seed == 4242


def test_a_missing_seed_falls_back_to_the_daily_one():
    assert _parse("").seed == params_service.daily_seed()


# ---------------------------------------------------------------------------
# Is this URL a facet?
# ---------------------------------------------------------------------------


def test_the_bare_search_page_is_not_a_facet():
    assert not params_service.is_faceted(QueryDict(""))


@pytest.mark.parametrize(
    "query", ["q=adhd", "speciality=a", "verified=1", "page=2", "radius=25", "near=KT18"]
)
def test_any_recognised_parameter_makes_it_a_facet(query):
    assert params_service.is_faceted(QueryDict(query))


def test_an_unrecognised_parameter_does_not():
    """A tracking parameter on an inbound link should not make the page noindex."""
    assert not params_service.is_faceted(QueryDict("utm_source=newsletter"))


# ---------------------------------------------------------------------------
# Building URLs back
# ---------------------------------------------------------------------------


def test_removing_one_value_keeps_the_others():
    query = QueryDict("speciality=a&speciality=b&verified=1")

    result = params_service.querystring_without(query, "speciality", "a")

    assert "speciality=b" in result
    assert "speciality=a" not in result
    assert "verified=1" in result


def test_removing_a_flag_drops_it_entirely():
    query = QueryDict("verified=1&speciality=a")

    result = params_service.querystring_without(query, "verified", None)

    assert "verified" not in result
    assert "speciality=a" in result


def test_changing_a_filter_returns_to_page_one():
    """Keeping the page number would land somebody on an empty page 4 of a
    three-page result set."""
    query = QueryDict("speciality=a&page=4")

    assert "page" not in params_service.querystring_without(query, "speciality", "a")
    assert "page" not in params_service.querystring_with(query, "radius", 25)


def test_a_pagination_link_carries_the_filters():
    query = QueryDict("speciality=a&page=1")

    result = params_service.querystring_for_page(query, 2)

    assert "speciality=a" in result
    assert "page=2" in result
    assert result.count("page=") == 1


def test_a_pagination_link_does_not_generate_a_seed():
    """The seed is derived from today's date, so page 2 computes the same one page 1
    did — it does not need to travel.

    Stamping it in created a fresh URL space every day: `num_pages × 365` distinct
    `?seed=…&page=…` URLs a year, all `noindex, follow`, so Googlebot would fetch
    and follow every one forever and never index a single result.
    """
    result = params_service.querystring_for_page(QueryDict("speciality=a"), 2)

    assert "seed=" not in result


def test_pagination_is_stable_without_the_seed_in_the_url():
    """The property the seed was in the URL to protect, protected without it."""
    page_one = params_service.parse(QueryDict("page=1"), page_size=20)
    page_two = params_service.parse(QueryDict("page=2"), page_size=20)

    assert page_one.seed == page_two.seed


def test_an_explicit_seed_still_survives_pagination():
    """Somebody who arrived on a shared link keeps that exact ordering."""
    result = params_service.querystring_for_page(QueryDict("seed=4242&page=1"), 2)

    assert "seed=4242" in result


def test_replacing_the_radius_keeps_every_other_filter():
    query = QueryDict("speciality=a&radius=10&verified=1")

    result = params_service.querystring_with(query, "radius", 25)

    assert "radius=25" in result
    assert "radius=10" not in result
    assert "speciality=a" in result
    assert "verified=1" in result


# ---------------------------------------------------------------------------
# The facet vocabulary
# ---------------------------------------------------------------------------


def test_every_facet_option_has_the_same_shape():
    """The component does no key bridging — `option.slug|default:option.code` is
    what took the page down with a VariableDoesNotExist during Phase 4."""
    from apps.directory.factories import (
        ApproachFactory,
        ClientGroupFactory,
        FundingOptionFactory,
        LanguageFactory,
        ProfessionFactory,
        SessionFormatFactory,
        SpecialityFactory,
    )

    ProfessionFactory(slug="p", name="P")
    ApproachFactory(slug="a", name="A")
    ClientGroupFactory(slug="g", name="G")
    LanguageFactory(code="en", name="English")
    FundingOptionFactory(slug="f", name="F")
    SessionFormatFactory(slug="s", name="S")
    SpecialityFactory(slug="sp", name="Sp")

    data = params_service.facets()

    for key in ("professions", "approaches", "client_groups", "languages", "funding", "session_formats"):
        assert data[key], f"{key} is empty"
        for option in data[key]:
            assert set(option) == {"value", "name"}, f"{key} has the wrong shape"

    for category in data["speciality_tree"]:
        assert set(category) == {"value", "name", "specialities"}
        for speciality in category["specialities"]:
            assert set(speciality) == {"value", "name"}


def test_a_cached_payload_from_an_older_shape_is_ignored():
    """A deploy that changes the shape used to read the old shape back out of Redis
    and 500 on a KeyError until somebody flushed the cache."""
    from django.core.cache import cache

    cache.set(params_service.FACET_CACHE_KEY, {"version": 0, "data": {"nonsense": True}})

    data = params_service.facets()

    assert "speciality_tree" in data


def test_a_cached_payload_of_the_current_shape_is_reused():
    from django.core.cache import cache

    sentinel = {"speciality_tree": [], "radii": [1]}
    cache.set(
        params_service.FACET_CACHE_KEY,
        {"version": params_service.FACET_CACHE_VERSION, "data": sentinel},
    )

    assert params_service.facets() == sentinel


def test_publishing_a_practitioner_clears_the_facet_cache():
    """The facet cache uses the key `publication.bust_cache()` already clears.

    A suspended listing must not linger in a facet list, and this asserts the two
    modules agree about the key rather than trusting a comment.
    """
    from apps.backoffice.services.publication import CACHE_KEY_PATTERNS

    assert params_service.FACET_CACHE_KEY in CACHE_KEY_PATTERNS
