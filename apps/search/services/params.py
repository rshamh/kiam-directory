"""Turning a query string into ``SearchParams``, and back again.

Kept out of the view so the parsing is testable without HTTP, and kept in one
place so the sidebar, the pagination links, the "remove this filter" chips and the
canonical URL all agree about what a parameter is called.

Two decisions live here.

**The shuffle seed rotates daily and is not a session.** The brief says "seeded
per session"; a session seed means writing a session cookie for every anonymous
visitor who runs a search, which would make ``/cookies/`` — which says the
sign-in cookie is set "only if you sign in" — untrue, on a site whose golden rule
#4 is minimal collection. A date-derived seed gets the property that actually
matters, stable ordering across pagination and reloads, without any state at all,
and still rotates so the same few practitioners cannot camp the top slots. It is
overridable with ``?seed=`` so a shared link reproduces exactly what the sender
saw, and so the ranking tests can pin it. Raised at the Phase 4 gate.

**Any recognised parameter makes the URL a facet.** ``docs/seo.md`` requires every
faceted search URL to be ``noindex, follow`` with a canonical to the bare
``/search/``. The rule here is deliberately blunt — a query string of any kind
means noindex — because the alternative is a list of "indexable" permutations that
someone will extend by one, and the failure mode is a doorway farm.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlencode

from django.core.cache import cache
from django.utils import timezone

from apps.directory.services.search import (
    DEFAULT_RADIUS_MILES,
    RADIUS_OPTIONS,
    WAIT_ORDER,
    SearchParams,
)

#: GET key -> SearchParams attribute, for the list-valued facets. Short, stable
#: names: these end up in URLs people share.
LIST_FACETS = {
    "speciality": "specialities",
    "category": "speciality_categories",
    "profession": "professions",
    "approach": "approaches",
    "group": "client_groups",
    "language": "languages",
    "funding": "funding",
    "format": "session_formats",
    "gender": "genders",
}

#: GET key -> SearchParams attribute, for the on/off facets.
FLAG_FACETS = {
    "accepting": "accepting_new_clients",
    "evening": "evening",
    "weekend": "weekend",
    "verified": "verified_only",
    "prescriber": "is_prescriber",
    "step_free": "step_free_access",
    "parking": "parking_available",
    "hearing_loop": "hearing_loop",
}

#: Everything the view reads. Presence of any of these makes the URL a facet.
RECOGNISED = frozenset(
    {"q", "near", "radius", "delivery", "wait", "fee_max", "experience", "page", "seed"}
    | set(LIST_FACETS)
    | set(FLAG_FACETS)
)

#: The same key `backoffice.services.publication.CACHE_KEY_PATTERNS` busts on
#: every publication change, so a suspended listing cannot linger in a facet count.
FACET_CACHE_KEY = "search:facets"
FACET_CACHE_SECONDS = 600

#: Bump when the SHAPE of the cached payload changes.
#:
#: Learned the hard way while building this: a deploy that changes the shape reads
#: the old shape back out of Redis and every request 500s on a KeyError until
#: somebody thinks to flush the cache. Versioning inside the value — rather than in
#: the key — keeps `publication.bust_cache()` working against the one key it knows,
#: and makes a shape change self-healing instead of a manual step in a runbook.
FACET_CACHE_VERSION = 2

#: How many funding rows the sidebar shows before the rest go behind a control.
#: Six is what fits panel 1 without the group becoming the panel.
FUNDING_ROWS_VISIBLE = 6

#: How many speciality categories are listed before "Browse all N categories".
#: Eighteen categories is ~840px of summary rows before a visitor has opened
#: anything; six is a list somebody can read.
CATEGORY_ROWS_VISIBLE = 6


def daily_seed(when=None) -> int:
    """A seed that is the same all day and different tomorrow.

    Derived from the date rather than random per request: a page-one reload that
    reshuffles is disorienting, and this audience has a high rate of anxiety and
    neurodevelopmental conditions (golden rule #3). Rotating daily still spreads
    exposure over time, which is what the banded shuffle is for.
    """
    day = (when or timezone.localdate()).isoformat()
    return int.from_bytes(hashlib.sha256(day.encode()).digest()[:4], "big")


def parse(query, *, page_size: int) -> SearchParams:
    """Read a ``QueryDict`` (or any multi-dict) into ``SearchParams``."""
    getlist = getattr(query, "getlist", None) or (lambda key: [v for v in [query.get(key)] if v])

    params = SearchParams(
        q=(query.get("q") or "").strip()[:200],
        radius_miles=_radius(query.get("radius")),
        page=_positive_int(query.get("page"), default=1),
        page_size=page_size,
        seed=_positive_int(query.get("seed"), default=None) or daily_seed(),
    )

    for key, attribute in LIST_FACETS.items():
        values = [v.strip() for v in getlist(key) if v and v.strip()]
        # Deduplicated and capped: a hand-built URL with two hundred repeated
        # facet values is a cheap way to make Postgres do a lot of work.
        setattr(params, attribute, list(dict.fromkeys(values))[:40])

    for key, attribute in FLAG_FACETS.items():
        setattr(params, attribute, _flag(query.get(key)))

    delivery = (query.get("delivery") or "").strip()
    params.in_person_only = delivery == "in_person"
    params.online_only = delivery == "online"

    wait = (query.get("wait") or "").strip()
    params.max_wait = wait if wait in WAIT_ORDER else ""

    params.fee_max_pence = _positive_int(query.get("fee_max"), default=None)
    params.min_years_experience = _positive_int(query.get("experience"), default=None)

    return params


def _radius(raw) -> int:
    """Only the offered radii. An arbitrary one is a hand-built URL, and a
    500-mile radius is a table scan dressed up as a search."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RADIUS_MILES
    return value if value in RADIUS_OPTIONS else DEFAULT_RADIUS_MILES


def _positive_int(raw, *, default):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _flag(raw) -> bool:
    return str(raw).lower() in {"1", "true", "on", "yes"}


# ---------------------------------------------------------------------------
# Describing the current search back to the visitor
# ---------------------------------------------------------------------------


def is_faceted(query) -> bool:
    """Whether this URL needs ``noindex, follow`` and a canonical to ``/search/``."""
    return any(key in RECOGNISED and query.get(key) for key in query)


def active_filters(params: SearchParams, query) -> list[dict]:
    """The filters currently applied, as removable chips.

    Labels come from the facet vocabulary rather than the slug, because "adhd-in-women"
    is not something to show a person who is already having a hard day.
    """
    labels = _facet_labels()
    chips = []

    for key, attribute in LIST_FACETS.items():
        for value in getattr(params, attribute):
            chips.append(
                {
                    "key": key,
                    "value": value,
                    "label": labels.get(key, {}).get(value, value),
                    "remove_url": querystring_without(query, key, value),
                }
            )

    for key, attribute in FLAG_FACETS.items():
        if getattr(params, attribute):
            chips.append(
                {
                    "key": key,
                    "value": "1",
                    "label": FLAG_LABELS[key],
                    "remove_url": querystring_without(query, key, None),
                }
            )

    if params.in_person_only or params.online_only:
        chips.append(
            {
                "key": "delivery",
                "value": "in_person" if params.in_person_only else "online",
                "label": "In person" if params.in_person_only else "Online",
                "remove_url": querystring_without(query, "delivery", None),
            }
        )

    if params.max_wait:
        chips.append(
            {
                "key": "wait",
                "value": params.max_wait,
                "label": f"Wait: {WAIT_LABELS.get(params.max_wait, params.max_wait)}",
                "remove_url": querystring_without(query, "wait", None),
            }
        )

    if params.fee_max_pence:
        chips.append(
            {
                "key": "fee_max",
                "value": str(params.fee_max_pence),
                "label": f"Up to £{params.fee_max_pence // 100}",
                "remove_url": querystring_without(query, "fee_max", None),
            }
        )

    if params.min_years_experience:
        chips.append(
            {
                "key": "experience",
                "value": str(params.min_years_experience),
                "label": f"{params.min_years_experience}+ years' experience",
                "remove_url": querystring_without(query, "experience", None),
            }
        )

    return chips


FLAG_LABELS = {
    "accepting": "Accepting new clients",
    "evening": "Evening appointments",
    "weekend": "Weekend appointments",
    "verified": "Verified only",
    "prescriber": "Can prescribe",
    "step_free": "Step-free access",
    "parking": "Parking available",
    "hearing_loop": "Hearing loop",
}

WAIT_LABELS = {
    "immediate": "within a week",
    "short": "1–2 weeks",
    "medium": "2–4 weeks",
    "long": "4+ weeks",
}


def querystring_without(query, key: str, value: str | None) -> str:
    """The current query string with one filter value removed.

    ``page`` is always dropped: changing the filters changes what is on page one,
    so keeping a page number would land somebody on an empty page 4 of a
    three-page result set.
    """
    pairs = []
    for existing_key in query:
        if existing_key == "page":
            continue
        for existing in query.getlist(existing_key):
            if existing_key == key and (value is None or existing == value):
                continue
            pairs.append((existing_key, existing))
    return "?" + urlencode(pairs) if pairs else ""


def querystring_with(query, key: str, value) -> str:
    """The current query string with one single-valued parameter replaced.

    Used by the empty state's "search further out" link, which has to keep every
    other filter intact — an offer to widen the radius that silently drops the
    speciality the visitor came for is not the same search.
    """
    pairs = [
        (existing_key, existing)
        for existing_key in query
        if existing_key not in {key, "page"}
        for existing in query.getlist(existing_key)
    ]
    pairs.append((key, str(value)))
    return "?" + urlencode(pairs)


def querystring_for_page(query, page: int) -> str:
    """A pagination link that reproduces this exact ordering.

    **The seed is deliberately NOT stamped in.** It does not need to be: it is
    derived from today's date, so page 2 computes the same seed page 1 did and the
    ordering is stable across pagination for free.

    Stamping it in was the first version, and it created a fresh URL space every
    day — ``num_pages × 365`` distinct ``?seed=…&page=…`` URLs a year, every one of
    them ``noindex, follow``, so Googlebot would fetch and follow all of them
    forever and never be able to index one. Caught by the Phase 4 SEO review.

    An explicit ``?seed=`` is still honoured when somebody arrives with one — that
    is what makes a shared link reproduce exactly what the sender saw. It is just
    not something we generate.
    """
    pairs = [(key, value) for key in query if key != "page" for value in query.getlist(key)]
    pairs.append(("page", str(page)))
    return "?" + urlencode(pairs)


# ---------------------------------------------------------------------------
# The sidebar's vocabulary
# ---------------------------------------------------------------------------


def facets() -> dict:
    """Every option the sidebar offers, cached.

    The taxonomy changes when somebody edits it, which is rarely, and this is
    eight queries on a page that has a latency budget. Cached under the key
    `publication.bust_cache()` already clears.
    """
    cached = cache.get(FACET_CACHE_KEY)
    if isinstance(cached, dict) and cached.get("version") == FACET_CACHE_VERSION:
        return cached["data"]

    from apps.directory.models import (
        Approach,
        ClientGroup,
        FundingOption,
        Language,
        Profession,
        SessionFormat,
        SpecialityCategory,
    )

    # Every option is normalised to {"value", "name"}. Languages key on their ISO
    # `code` and everything else on `slug`, and the template used to bridge that
    # with `option.slug|default:option.code` — which raises, because the `default`
    # filter resolves its argument eagerly, so a client group with no `code` blew
    # the page up. One shape here means the component has nothing to bridge.
    data = {
        # The two-level tree: categories, each with its own specialities. Rendered
        # as a nested list of real checkboxes, so it works as a plain form.
        "speciality_tree": [
            {
                "value": category.slug,
                "name": category.name,
                "specialities": [
                    {"value": s.slug, "name": s.name} for s in category.specialities.all() if s.active
                ],
            }
            for category in SpecialityCategory.objects.filter(active=True).prefetch_related("specialities")
        ],
        "professions": _options(Profession.objects.filter(active=True)),
        "approaches": _options(Approach.objects.filter(active=True)),
        "client_groups": _options(ClientGroup.objects.filter(active=True)),
        "languages": _options(Language.objects.filter(active=True), key="code"),
        "funding": _options(FundingOption.objects.filter(active=True)),
        "session_formats": _options(SessionFormat.objects.filter(active=True)),
        "radii": RADIUS_OPTIONS,
    }
    cache.set(
        FACET_CACHE_KEY,
        {"version": FACET_CACHE_VERSION, "data": data},
        timeout=FACET_CACHE_SECONDS,
    )
    return data


def facets_with_counts(counts: dict, params: SearchParams | None = None) -> dict:
    """The cached vocabulary with this search's counts and selection written onto it.

    A COPY, always, and that is the whole point of the function existing rather
    than the view mutating what `facets()` hands back. The vocabulary is shared
    and cached; a per-request count written onto it is one visitor's numbers
    served to everybody, and `FACET_CACHE_VERSION` would not catch it because the
    shape is unchanged — only the meaning. That is exactly the failure the
    constant's docstring was widened to cover at Phase 5.

    Copying eighteen categories and a hundred and fifty specialities per request
    is a dict comprehension. The bug is not that cheap.

    ``selected`` per category is here rather than in the template for the Phase 4
    reason: a collapsed ``<details>`` removes its contents from the accessibility
    tree, so a category holding a ticked speciality has to render OPEN or arriving
    from a shared URL hides an applied filter rather than merely scrolling past
    it. "Does any speciality in this category appear in the selection" is not
    something a Django template can ask, and a template that cannot ask it
    silently answers no.

    ``speciality_total`` is what the filter box says it searches. Counted from the
    live vocabulary rather than written into the copy, so retiring a term does not
    leave the placeholder claiming a number that has not been true for a year.
    """
    data = facets()
    speciality_counts = counts.get("speciality", {})
    category_counts = counts.get("category", {})
    funding_counts = counts.get("funding", {})

    chosen_specialities = set(params.specialities if params else [])
    chosen_categories = set(params.speciality_categories if params else [])

    tree = []
    for category in data["speciality_tree"]:
        specialities = [
            {**speciality, "count": speciality_counts.get(speciality["value"], 0)}
            for speciality in category["specialities"]
        ]
        tree.append(
            {
                **category,
                "count": category_counts.get(category["value"], 0),
                "specialities": specialities,
                "selected": category["value"] in chosen_categories
                or any(speciality["value"] in chosen_specialities for speciality in specialities),
                # The needle the client-side filter box matches against, built
                # here so the template does not join a hundred and fifty strings
                # in a loop. Lower-cased once rather than per keystroke.
                "haystack": " ".join(
                    [category["name"], *(speciality["name"] for speciality in specialities)]
                ).lower(),
            }
        )

    funding = [{**option, "count": funding_counts.get(option["value"], 0)} for option in data["funding"]]
    chosen_funding = set(params.funding if params else [])

    # A selected category is pulled to the FRONT rather than left in the tail. It
    # keeps the applied filter in the accessibility tree without forcing the
    # overflow open, and it is the only reordering the sidebar does — everything
    # else stays in taxonomy order, because a filter list that rearranges itself
    # under somebody mid-task is what golden rule #3 rules out.
    tree.sort(key=lambda category: not category["selected"])

    merged = dict(data)
    merged["speciality_tree"] = tree
    merged["speciality_visible"] = tree[:CATEGORY_ROWS_VISIBLE]
    merged["speciality_more"] = tree[CATEGORY_ROWS_VISIBLE:]
    merged["speciality_total"] = sum(len(category["specialities"]) for category in tree)
    merged["funding"] = funding
    # The seeded taxonomy has fifteen insurers and schemes, which is a fifteen-row
    # wall in the panel a visitor reads first. The rest go behind one control.
    #
    # The split is here and not in the template for the same reason `selected` is:
    # the group has to render OPEN when the overflow holds a selection, or a
    # ticked "NHS Right to Choose" arriving from a shared URL is inside a closed
    # <details> and therefore out of the accessibility tree entirely. A template
    # cannot ask "is any of the tail selected".
    merged["funding_visible"] = funding[:FUNDING_ROWS_VISIBLE]
    merged["funding_more"] = funding[FUNDING_ROWS_VISIBLE:]
    merged["funding_more_selected"] = any(
        option["value"] in chosen_funding for option in merged["funding_more"]
    )
    return merged


def _options(queryset, key: str = "slug") -> list[dict]:
    """A facet's options in the one shape the sidebar component understands."""
    return [{"value": value, "name": name} for value, name in queryset.values_list(key, "name")]


def _facet_labels() -> dict:
    """Facet value -> human label, for the removable chips."""
    data = facets()
    tree = data["speciality_tree"]
    return {
        "speciality": {s["value"]: s["name"] for c in tree for s in c["specialities"]},
        "category": {c["value"]: c["name"] for c in tree},
        "profession": {p["value"]: p["name"] for p in data["professions"]},
        "approach": {a["value"]: a["name"] for a in data["approaches"]},
        "group": {g["value"]: g["name"] for g in data["client_groups"]},
        "language": {lang["value"]: lang["name"] for lang in data["languages"]},
        "funding": {f["value"]: f["name"] for f in data["funding"]},
        "format": {f["value"]: f["name"] for f in data["session_formats"]},
    }
