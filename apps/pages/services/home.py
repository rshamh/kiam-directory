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
cache: a change is then self-healing rather than a manual flush in a runbook.

**Bump it when the SHAPE or the MEANING of a payload changes**, and the second half
of that sentence is there because Phase 5's own gate review caught this module
failing its own rule. Two fixes landed — ``count_label`` was added to the browse
dicts, and ``homepage_grid()`` started applying ``FEATURED_CAP_PER_PAGE`` — and the
version was not bumped. The shape check passed, the old payload was served, and the
live page rendered browse counts with no unit at all next to four "Paid placement"
cards under a sentence promising no more than three. Both fixes were correct and
both were invisible for as long as the entry lived. A selection rule is as much a
part of a cached payload as its keys are.

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
from collections import Counter
from urllib.parse import urlencode

from django.core.cache import cache
from django.db.models import Count
from django.urls import reverse

from apps.directory.models import (
    Practitioner,
    PractitionerLocation,
    Profession,
    PublicationStatus,
    SpecialityCategory,
)
from apps.directory.services import images
from apps.directory.services import search as search_service
from apps.search.services import params as params_service
from apps.search.services.geocode import OUTCODE_RE

logger = logging.getLogger("pages.home")

#: One of ``backoffice.services.publication.CACHE_KEY_PATTERNS``, so every
#: publish / suspend / unpublish already drops it. Do not rename without renaming
#: it there — the whole "takes effect within seconds" promise runs through that
#: list.
GRID_CACHE_KEY = "homepage:grid"
BROWSE_CACHE_KEY = "homepage:browse"
HERO_CACHE_KEY = "homepage:hero"

#: 24 hours, matching the rotation. A shorter TTL would rebuild the same twelve;
#: a longer one would outlive the seed that chose them.
CACHE_SECONDS = 60 * 60 * 24

#: Bump when the SHAPE **or the MEANING** of a cached payload changes — a new key, a
#: renamed one, or a change to the rule that chose what is in it. See the module
#: docstring for the deploy this caught.
#:
#: 2: Phase 5 gate. ``count_label`` added to the browse dicts, and the grid
#:    selection began applying ``FEATURED_CAP_PER_PAGE`` and excluding listings that
#:    advertise under-18 work without a cleared DBS.
#: 3: The hero gained its own cached payload — the profession chips and the
#:    "N practitioners listed" count.
CACHE_VERSION = 3

GRID_SIZE = 12

#: Profession chips under the hero search bar. Four, because the row has to stay on
#: one line at 320px without becoming a scroller, and because four is what the
#: lede already names.
#:
#: They are counted from PUBLISHED LISTINGS, never taken from ``taxonomy.py`` — the
#: same rule as the browse links and for the same reason. The taxonomy has 29
#: professions; picking four of them by hand would put "Music Therapist" on the
#: front page of a directory that has none listed, and every chip has to land on a
#: result set that is not empty.
HERO_PROFESSION_LIMIT = 4

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
        **_hero_extras(),
    }


def _hero_extras() -> dict:
    """The chips under the bar and the count under them. Cached together.

    Both answer the same question — "is there anything here for me, and can I start
    without typing" — and both are counted from published listings, so they live in
    one payload under one key that ``publication.bust_cache()`` clears.
    """
    payload = cache.get(HERO_CACHE_KEY)
    if isinstance(payload, dict) and payload.get("version") == CACHE_VERSION:
        return payload["data"]

    data = {
        "hero_professions": _profession_links(),
        "listing_count": Practitioner.objects.filter(status=PublicationStatus.PUBLISHED).count(),
    }
    cache.set(HERO_CACHE_KEY, {"version": CACHE_VERSION, "data": data}, timeout=CACHE_SECONDS)
    return data


def _profession_links() -> list[dict]:
    """The four professions with the most published listings.

    **Ordered by how many people are actually listed, not by the taxonomy's
    ``sort_order``.** A chip is a promise that clicking it returns something, and
    the ordering that keeps that promise strongest is the one that puts the fullest
    result set first.

    ``noindex, follow`` like every other facet URL on this page (``seo.views``
    handles that for ``/search/``), and capped at four rather than "all with
    listings" for the doorway-pattern reason in ``BROWSE_LIMIT``'s docstring.

    **No under-18 gate needed here, and that is a fact about professions rather
    than luck.** The gate exists because a *speciality* can carry
    ``implies_minors`` and a *client group* can carry ``is_minors``; a profession
    carries neither, and the destination is ``/search/``, which applies both halves
    of the gate to whatever the visitor asked for. A chip labelled "Child &
    Adolescent Psychiatrist" would be a different question — it does not appear
    here because it is a profession slug, and if the taxonomy ever gives
    ``Profession`` an ``implies_minors`` flag this function has to grow the same
    exclusion ``search._ungated_minor_work_ids`` applies to the grid.
    """
    professions = (
        Profession.objects.filter(active=True, practitioners__status=PublicationStatus.PUBLISHED)
        .annotate(listed=Count("practitioners", distinct=True))
        .order_by("-listed", "name")
        .values("slug", "name", "listed")[:HERO_PROFESSION_LIMIT]
    )
    return [
        {
            "label": row["name"],
            "count": row["listed"],
            "url": _search_url(profession=row["slug"]),
        }
        for row in professions
    ]


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

    **Both safety filters are re-applied here, not only the selection-time ones**,
    and the second was added at the Phase 6 SEO review.

    ``status=PUBLISHED`` covers a suspension that outran ``bust_cache()``.

    The under-18 exclusion covers something Phase 5 could not have needed: back
    then, only staff could add an ``implies_minors`` speciality to a live listing,
    so filtering at selection time was enough. The dashboard's taxonomy editor made
    that self-service and immediate — and ``specialities`` is a SAFE field, so it
    publishes on Save. A practitioner already inside the cached twelve who ticked
    "Child & adolescent ADHD assessment" had the pill rendered on the home page,
    under Kiam's own editorial selection, until the entry expired. Reproduced
    before it was fixed.

    ``bust_cache()`` on the edit path is the primary mechanism and is now called.
    This is the fail-safe that makes it non-load-bearing — the same reasoning as
    the ``PUBLISHED`` re-check, which the module docstring already sets out.

    The ordering comes from ``ids`` rather than from the database, because ``__in``
    does not preserve it and the order is the whole point of the daily shuffle.
    """
    if not ids:
        return []

    rows = {
        str(p.pk): p
        for p in Practitioner.objects.filter(pk__in=ids, status=PublicationStatus.PUBLISHED)
        .exclude(pk__in=search_service._ungated_minor_work_ids())
        .select_related("profession")
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

    **No minimum count, unlike the town links, and the asymmetry is deliberate.**
    A category with one listing is a link to one result: thin, but accurate, and the
    count next to it sets that expectation. The town floor exists for a reason
    categories do not have — naming a town with one practitioner in it publishes
    where that person works. Phase 7's landing pages apply the stricter ≥3 rule to
    both, because a *page* makes a claim a link does not.

    **Phase 7 dependency.** These anchors are the anchors the curated
    ``/[speciality]/[town]`` pages will want. Re-point this function and
    ``_town_links()`` at them when they exist, or the landing pages launch with no
    internal links from the strongest page on the subdomain.
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
        {
            "label": row["name"],
            "count": row["listed"],
            # Accurate as a result count: the destination filters on the same
            # category and applies no radius, so "10 listed" lands on 10 results.
            "count_label": "listed",
            "url": _search_url(category=row["slug"]),
        }
        for row in categories
    ]


def _town_links() -> list[dict]:
    """Towns with a public practice address and enough practitioners to be useful.

    ``is_public=False`` addresses are excluded. Those exist so a practitioner can be
    found by radius without publishing where they work — usually a home office — and
    naming that town in a browse link would publish it by inference.

    **The link centres on an OUTWARD CODE, not on the town name, and that is a
    correction from the Phase 5 SEO review.** ``?near=Croydon`` looked obvious and
    was wrong: ``geocode.places()`` takes the first OS Open Names match with no
    importance ranking, so three of the ten town links resolved to the wrong
    settlement entirely — Croydon, *Cambridgeshire*; Brighton, *Cornwall*;
    Guildford, *Pembrokeshire*. A person typing "Guildford" sees the resolved label
    and can correct it; a link on the home page **asserts** the destination, so the
    weakness became a defect the moment Phase 5 authored the anchor.

    An outward code goes through ``geocode`` 's ``/outcodes/`` path, which is an
    exact lookup rather than a prefix search, so "CR0" is Croydon and cannot be
    anywhere else. It is also not a household — ``search/views.py`` already logs
    only the outward code, for that reason — so this publishes no more about a
    practice address than the town name already did. Note the ``county`` column is
    *not* usable for disambiguation: the demo seed has Croydon in West Yorkshire,
    and nothing lints it.

    ``delivery=in_person`` is on the link because the heading is "By where they
    work". Without it the radius search ORs in ``offers_online=True``
    (``directory/services/search.py``), so "Epsom — 3 based here" landed on
    twenty-five results and read as a broken filter.

    Grouped in Python rather than by ``annotate``: picking the modal outward code
    per town is not something one aggregate expresses, and this runs once per
    24-hour cache entry over a small table.
    """
    rows = PractitionerLocation.objects.filter(
        is_public=True, practitioner__status=PublicationStatus.PUBLISHED
    ).values_list("city", "postcode", "practitioner_id")

    towns: dict[str, dict] = {}
    for city, postcode, practitioner_id in rows:
        city = (city or "").strip()
        if not city:
            continue
        town = towns.setdefault(city, {"practitioners": set(), "outcodes": Counter()})
        town["practitioners"].add(practitioner_id)
        outcode = _outward_code(postcode)
        if outcode:
            town["outcodes"][outcode] += 1

    links = []
    for city, town in towns.items():
        count = len(town["practitioners"])
        if count < MIN_PRACTITIONERS_PER_TOWN or not town["outcodes"]:
            # No usable outward code means no link. Falling back to `?near=<city>`
            # would reintroduce the wrong-Croydon bug on exactly the towns whose
            # data is weakest.
            continue
        outcode = town["outcodes"].most_common(1)[0][0]
        links.append(
            {
                "label": city,
                "count": count,
                # NOT "listed". The number counts practitioners with an address in
                # this town; the destination is everyone within ten miles of its
                # outward code, which is legitimately more. "3 listed" landing on
                # nine results reads as a broken filter, so the label says what the
                # number actually is and promises no result count.
                "count_label": "based here",
                "url": _search_url(near=outcode, delivery="in_person"),
            }
        )

    links.sort(key=lambda link: (-link["count"], link["label"]))
    return links[:BROWSE_LIMIT]


def _outward_code(postcode: str) -> str:
    """ "KT18 5EP" -> "KT18", or ``""`` if it is not an outward code.

    Validated against ``geocode.OUTCODE_RE`` rather than a second pattern here, so
    the one definition of "outward code" in this project is the one the geocoder
    routes on.
    """
    head = (postcode or "").strip().upper().split(" ")[0]
    return head if OUTCODE_RE.match(head) else ""


def _search_url(**query) -> str:
    """A search URL, reversed and encoded.

    Not an f-string: a town called "Newcastle upon Tyne" or a slug with an
    ampersand in it has to survive the trip, and ``/search/`` is not a path this
    module gets to hard-code (``pages/tests/test_chrome.py`` walks reversed URLs
    for exactly this reason).
    """
    return f"{reverse('search:search')}?{urlencode(query)}"
