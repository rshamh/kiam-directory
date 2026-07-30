"""Turning what somebody typed into a point on the map.

Two lookups, both against **postcodes.io**, both keyless and free:

* ``/postcodes/{postcode}`` — a UK postcode to a latitude and longitude.
* ``/places?q=`` — place-name autocomplete. This is **OS Open Names** data;
  postcodes.io serves it, which is what lets us have OS Open Names without an OS
  Data Hub key in the deployment and without a Google Maps bill (CLAUDE.md).

Three rules, and the third is the one that matters most.

**Cache aggressively.** A postcode's coordinates do not change. Successful
lookups are held for a month, place searches for a week, so a busy search page
makes almost no outbound calls and the third-party service is not in the hot path
of every keystroke.

**Cache failures too, briefly.** A typo that resolves to nothing would otherwise
hit the API on every retry. A short negative TTL stops that without blacklisting
a genuinely new postcode for a month.

**Never let a geocoder break search.** Every failure — timeout, DNS, 500, garbage
JSON, the service being down entirely — returns ``None``. The caller then searches
*without* a location and says so, because a directory that answers "we could not
reach our mapping provider" to somebody looking for a therapist has failed at the
only job it has. `docs/seo.md` also puts a latency budget on this page; a
third-party call with no timeout is the fastest way to blow it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger("search.geocode")

#: UK postcodes, loosely. Deliberately permissive about spacing and case — it is a
#: routing hint for which endpoint to call, not a validator. postcodes.io is the
#: authority on whether a postcode exists.
POSTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$", re.IGNORECASE)

#: An outward code on its own — "KT18", "SW1A". Common in search boxes, and
#: postcodes.io resolves it through the outcodes endpoint.
OUTCODE_RE = re.compile(r"^[A-Z]{1,2}\d[A-Z\d]?$", re.IGNORECASE)

CACHE_PREFIX = "geocode"


@dataclass(frozen=True)
class Place:
    """A resolved location, and how it should be described back to the user."""

    label: str
    lat: float
    lng: float
    #: postcode · outcode · place. Shown in the location field so somebody can see
    #: that "Epsom" was read as a town and not as a postcode.
    kind: str = "place"


def looks_like_postcode(text: str) -> bool:
    return bool(POSTCODE_RE.match((text or "").strip()))


def looks_like_outcode(text: str) -> bool:
    return bool(OUTCODE_RE.match((text or "").strip()))


def resolve(text: str) -> Place | None:
    """Best effort: a point for whatever the visitor typed, or ``None``.

    ``None`` is a normal answer, not an error. The search view treats it as "no
    location given" and tells the visitor their location was not recognised while
    still showing them results.
    """
    text = (text or "").strip()
    if not text:
        return None

    if looks_like_postcode(text):
        return _postcode(text)
    if looks_like_outcode(text):
        return _outcode(text)

    found = places(text, limit=1)
    return found[0] if found else None


def places(query: str, limit: int = 8) -> list[Place]:
    """Place-name autocomplete, for the location combobox.

    Returns ``[]`` on any failure — an autocomplete that raises takes the whole
    page with it, and an empty list degrades to "type it yourself".
    """
    query = (query or "").strip()
    if len(query) < 2:
        return []

    key = f"{CACHE_PREFIX}:places:{query.lower()}:{limit}"
    cached = cache.get(key)
    if cached is not None:
        return [Place(**entry) for entry in cached]

    payload = _get("/places", params={"q": query, "limit": limit})
    results = []
    for entry in (payload or {}).get("result") or []:
        lat, lng = entry.get("latitude"), entry.get("longitude")
        if lat is None or lng is None:
            continue
        results.append(Place(label=_place_label(entry), lat=float(lat), lng=float(lng), kind="place"))

    cache.set(
        key,
        [r.__dict__ for r in results],
        timeout=(settings.GEOCODE_CACHE_SECONDS if results else settings.GEOCODE_NEGATIVE_CACHE_SECONDS),
    )
    return results


def _place_label(entry: dict) -> str:
    """ "Epsom, Surrey" rather than "Epsom".

    Two places share a name often enough that a bare one is ambiguous, and the
    visitor has to be able to tell which one the radius is centred on.
    """
    name = entry.get("name_1") or entry.get("name") or ""
    county = entry.get("county_unitary") or entry.get("region") or ""
    return f"{name}, {county}" if county and county != name else name


def _postcode(postcode: str) -> Place | None:
    normalised = re.sub(r"\s+", "", postcode).upper()
    key = f"{CACHE_PREFIX}:postcode:{normalised}"

    cached = cache.get(key)
    if cached is not None:
        # A cached miss is stored as False, which is not None — so a known-bad
        # postcode is answered from cache rather than re-fetched.
        return Place(**cached) if cached else None

    payload = _get(f"/postcodes/{normalised}")
    result = (payload or {}).get("result") or {}
    place = None
    if result.get("latitude") is not None and result.get("longitude") is not None:
        place = Place(
            label=result.get("postcode") or normalised,
            lat=float(result["latitude"]),
            lng=float(result["longitude"]),
            kind="postcode",
        )

    # Only cache a definite miss. `payload is None` means we never got an answer —
    # caching that would turn a thirty-second outage into a month of empty results.
    if place is not None:
        cache.set(key, place.__dict__, timeout=settings.GEOCODE_CACHE_SECONDS)
    elif payload is not None:
        cache.set(key, False, timeout=settings.GEOCODE_NEGATIVE_CACHE_SECONDS)

    return place


def _outcode(outcode: str) -> Place | None:
    normalised = outcode.strip().upper()
    key = f"{CACHE_PREFIX}:outcode:{normalised}"

    cached = cache.get(key)
    if cached is not None:
        return Place(**cached) if cached else None

    payload = _get(f"/outcodes/{normalised}")
    result = (payload or {}).get("result") or {}
    place = None
    if result.get("latitude") is not None and result.get("longitude") is not None:
        place = Place(
            label=result.get("outcode") or normalised,
            lat=float(result["latitude"]),
            lng=float(result["longitude"]),
            kind="outcode",
        )

    if place is not None:
        cache.set(key, place.__dict__, timeout=settings.GEOCODE_CACHE_SECONDS)
    elif payload is not None:
        cache.set(key, False, timeout=settings.GEOCODE_NEGATIVE_CACHE_SECONDS)

    return place


def _get(path: str, params: dict | None = None) -> dict | None:
    """One HTTP call, and it cannot raise.

    ``None`` means "no answer" and is distinct from an answer of "nothing found":
    the caller uses that difference to decide whether the miss is worth caching.

    A bare ``except Exception`` is deliberate. The set of ways an outbound HTTP
    call can fail is not enumerable — DNS, TLS, a proxy returning HTML, a JSON
    body that is not JSON — and every one of them has the same correct response
    here, which is to shrug and let the visitor search without a location.
    """
    url = f"{settings.GEOCODE_BASE_URL.rstrip('/')}{path}"
    try:
        response = requests.get(
            url,
            params=params,
            timeout=settings.GEOCODE_TIMEOUT_SECONDS,
            headers={"User-Agent": settings.GEOCODE_USER_AGENT},
        )
    except Exception:
        logger.warning("geocode.unreachable", extra={"path": path}, exc_info=True)
        return None

    if response.status_code == 404:
        # postcodes.io answers 404 for "no such postcode". That IS an answer, and
        # a cacheable one — it is not the service being unavailable.
        return {"result": None}
    if response.status_code >= 400:
        logger.warning("geocode.error_status", extra={"path": path, "status": response.status_code})
        return None

    try:
        return response.json()
    except ValueError:
        logger.warning("geocode.bad_json", extra={"path": path})
        return None
