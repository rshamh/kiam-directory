"""What the home page shows, and how long it is allowed to remember it.

Three things come out of here: the hero search bar's context, the daily-rotating
grid of twelve, and the browse entry points. All three are the same for every
visitor, which is the property that makes them cacheable — and the reason this
module holds no reference to ``request`` anywhere.

**What is cached is the SELECTION, not the rows and not the HTML.**

``homepage_grid()`` is the expensive part: a filter, an MD5 shuffle over the whole
published set and an ORDER BY. What comes back is twelve primary keys, and those
are what go into Redis. The rows themselves are re-read on every request.

That costs two extra indexed queries per home-page render and buys three things
that matter more:

* **A stale cache cannot publish a suspended listing.** The hydration query
  re-applies ``status=PUBLISHED``, so even if ``publication.bust_cache()`` were
  never called — a wrong Redis instance, a key typo, a future refactor — a
  suspended practitioner drops out of the grid on the next request rather than
  lingering for up to 24 hours. `docs/verification-policy.md`'s worst case is a
  struck-off practitioner live on a Kiam-branded page, and the home page is the
  most-visited page on the subdomain. Belt and braces on that is cheap.
* **Compliance copy is never stale.** Caching the rendered grid would freeze the
  verification badge's wording, the "Paid placement" label and the independence
  notice for a day after a Dr. Abbass-signed reword. Caching model *instances*
  would freeze the practitioner's own name and specialities the same way.
* **A migration cannot turn Redis into a 500.** Pickled model instances read back
  after a schema change raise on unpickle, and the page then 500s until somebody
  thinks to flush the cache. That is not hypothetical: it is exactly what
  ``search.services.params.FACET_CACHE_VERSION`` exists to prevent, and it
  happened while Phase 4 was being built. Primary keys have no schema.

``CACHE_VERSION`` is inside the payload for the same reason it is in the facet
cache: a shape change is then self-healing rather than a manual flush in a
runbook.

**The one thing the cache miss does that is not free** is building headshot
renditions (``directory.services.images``) — up to twelve images × three widths of
Pillow work on one unlucky request, once a day. That is the deliberate trade: the
alternative is either doing it on every request, or a nightly command whose
failure is silent. Timed at 12 practitioners with 2000px JPEGs it is well under a
second; the cache miss is logged with its duration so a regression is visible
rather than inferred.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlencode

from django.core.cache import cache
from django.db.models import Count
from django.urls import reverse

from directory.models import Practitioner, PractitionerLocation, PublicationStatus, SpecialityCategory
from directory.services import images
from directory.services import search as search_service
from search.services import params as params_service

logger = logging.getLogger("pages.home")

#: One of ``backoffice.services.publication.CACHE_KEY_PATTERNS``, so every
#: publish / suspend / unpublish already drops it. Do not rename without renaming
#: it there — the whole "takes effect within seconds" promise runs through that
#: list.
GRID_CACHE_KEY = "homepage:grid"
BROWSE_CACHE_KEY = "homepage:browse"

#: 24 hours, matching the rotation. A shorter TTL would rebuild the same twelve;
#: a longer one would outlive the seed that chose them.
CACHE_SECONDS = 60 * 60 * 24

#: Bump when the SHAPE of a cached payload changes. See the module docstring.
CACHE_VERSION = 1

GRID_SIZE = 12

#: How many browse links each group offers. A cap rather than "all of them"
#: because every one of these is a `noindex, follow` facet URL until Phase 7
#: replaces them with curated landing pages, and a home page fanning out into
#: dozens of thin permutations is the doorway pattern `docs/seo.md` forbids.
BROWSE_LIMIT = 12

#: A town needs this many published practitioners before it is offered as a
#: browse link. One practitioner behind a "Practitioners in Guildford" link is a
#: dead end for the visitor, and it also names a single person by their town.
MIN_PRACTITIONERS_PER_TOWN = 2


# ---------------------------------------------------------------------------
# The hero
# ---------------------------------------------------------------------------


def hero() -> dict:
    """Exactly the context ``components/_search_bar.html`` reads.

    The same partial the search page uses, with empty parameters — so the hero
    search box and the one on ``/search/`` are one implementation of the keyboard
    pattern, the datalist autocomplete and the radius select, not two that have to
    be kept in step. The form around it is a plain GET to ``/search/``
    (``templates/pages/home.html``), which is what makes it work with JavaScript
    off.
    """
    return {
        "params": search_service.SearchParams(),
        "facets": params_service.facets(),
        "near": "",
        "location": None,
        "location_failed": False,
    }


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def grid(size: int = GRID_SIZE) -> list[Practitioner]:
    """Twelve published practitioners, rotating daily, ready for the card partial."""
    payload = cache.get(GRID_CACHE_KEY)

    if not (isinstance(payload, dict) and payload.get("version") == CACHE_VERSION):
        payload = _build_grid_payload(size)
        cache.set(GRID_CACHE_KEY, payload, timeout=CACHE_SECONDS)

    return _hydrate(payload["ids"], payload["srcsets"])


def _build_grid_payload(size: int) -> dict:
    started = time.monotonic()

    chosen = list(search_service.homepage_grid(size))
    srcsets = {str(p.pk): images.srcset(p.headshot) for p in chosen if p.headshot}

    logger.info(
        "home.grid_rebuilt",
        extra={
            "count": len(chosen),
            "renditions": sum(1 for value in srcsets.values() if value),
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return {
        "version": CACHE_VERSION,
        "ids": [str(p.pk) for p in chosen],
        "srcsets": srcsets,
    }


def _hydrate(ids: list[str], srcsets: dict[str, str]) -> list[Practitioner]:
    """Re-read the chosen rows, in the chosen order, from the live table.

    ``status=PUBLISHED`` is re-applied here deliberately — see the module
    docstring. The ordering comes from ``ids`` rather than from the database,
    because ``__in`` does not preserve it and the order is the whole point of the
    daily shuffle.
    """
    if not ids:
        return []

    rows = {
        str(p.pk): p
        for p in Practitioner.objects.filter(pk__in=ids, status=PublicationStatus.PUBLISHED).select_related(
            "profession"
        )
    }
    items = [rows[pk] for pk in ids if pk in rows]

    # No `radius_miles`: nobody has given a location, so no card shows a distance
    # and every card with online sessions falls back to "Online".
    search_service.decorate_cards(items)

    for item in items:
        item.headshot_srcset = srcsets.get(str(item.pk), "")
        item.headshot_sizes = images.HEADSHOT_SIZES

    return items


# ---------------------------------------------------------------------------
# Browse entry points
# ---------------------------------------------------------------------------


def browse_entry_points() -> dict:
    """Where to go next, by speciality and by town.

    **Both lists are derived from published listings, never from the taxonomy
    alone.** A "Trauma" link that leads to an empty result set is worse than no
    link: it is the thin-page failure from the crawler's side and a dead end from
    the visitor's. Phase 7 replaces these with curated ``/[speciality]/[town]``
    landing pages, which have a stricter version of the same rule (≥3 published
    practitioners *and* a signed-off editorial intro).

    Cached under its own key, which ``publication.bust_cache()`` clears, so a
    suspension that empties a town removes the link within seconds.
    """
    payload = cache.get(BROWSE_CACHE_KEY)
    if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION:
        return payload["data"]

    data = {"specialities": _speciality_links(), "towns": _town_links()}
    cache.set(BROWSE_CACHE_KEY, {"version": CACHE_VERSION, "data": data}, timeout=CACHE_SECONDS)
    return data


def _speciality_links() -> list[dict]:
    """Speciality CATEGORIES, not the 138 individual specialities.

    Eighteen categories is a browsable list; 138 specialities is the filter
    sidebar, which already exists one click away and which Phase 4 had to collapse
    to stop it being a wall of 317 controls.
    """
    categories = (
        SpecialityCategory.objects.filter(
            active=True,
            specialities__active=True,
            specialities__practitioners__status=PublicationStatus.PUBLISHED,
        )
        .annotate(listed=Count("specialities__practitioners", distinct=True))
        .order_by("sort_order", "name")
        .values("slug", "name", "listed")[:BROWSE_LIMIT]
    )
    return [
        {"label": row["name"], "count": row["listed"], "url": _search_url(category=row["slug"])}
        for row in categories
    ]


def _town_links() -> list[dict]:
    """Towns with a public practice address and enough practitioners to be useful.

    ``is_public=False`` addresses are excluded. Those exist so a practitioner can
    be found by radius without publishing where they work — usually a home office
    — and naming that town in a browse link would publish it by inference.

    The link is ``?near=<town>``, which the search view geocodes. That is one
    outbound call the first time anyone follows it and a month of cache
    afterwards (``search.services.geocode``), which is cheaper than storing
    coordinates for a town list that changes when somebody moves house.
    """
    rows = (
        PractitionerLocation.objects.filter(is_public=True, practitioner__status=PublicationStatus.PUBLISHED)
        .values("city")
        .annotate(listed=Count("practitioner", distinct=True))
        .filter(listed__gte=MIN_PRACTITIONERS_PER_TOWN)
        .order_by("-listed", "city")[:BROWSE_LIMIT]
    )
    return [
        {"label": row["city"], "count": row["listed"], "url": _search_url(near=row["city"])} for row in rows
    ]


def _search_url(**query) -> str:
    """A search URL, reversed and encoded.

    Not an f-string: a town called "Newcastle upon Tyne" or a slug with an
    ampersand in it has to survive the trip, and ``/search/`` is not a path this
    module gets to hard-code (``pages/tests/test_chrome.py`` walks reversed URLs
    for exactly this reason).
    """
    return f"{reverse('search:search')}?{urlencode(query)}"
