"""The search page. Thin — parsing is in ``search/services/params.py``, the query
is in ``directory/services/search.py``, geocoding is in
``search/services/geocode.py``.

Two things about this view are requirements rather than choices.

**A normal GET returns the whole page; an HTMX GET returns the results fragment.**
Same URL, same parameters, same queryset — the only difference is how much HTML
comes back. That is what keeps ``/search/`` crawlable and keyboard-usable, and it
is why the sidebar is a real ``<form>`` with a real submit button. HTMX is the
enhancement, not the mechanism (CLAUDE.md, "HTMX conventions").

**A geocoding failure is not a search failure.** If the location cannot be
resolved — bad postcode, or postcodes.io is down — the search runs without it and
the page says so. A directory that answers "we could not reach our mapping
provider" to somebody looking for a therapist has failed at the only job it has.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.http import require_GET

from apps.directory.models import Gender
from apps.directory.services import metrics
from apps.directory.services import search as search_service
from apps.seo import jsonld

from .services import geocode
from .services import params as params_service

logger = logging.getLogger("search")

#: Offered as "at least N years". Round numbers a person would actually pick,
#: rather than a free-text box that invites "0" and "99".
EXPERIENCE_OPTIONS = [2, 5, 10, 15, 20]

#: Fee ceilings, in pounds, stored in pence. Chosen around what UK private
#: sessions actually cost so the filter has somewhere useful to land.
FEE_OPTIONS = [
    {"pounds": 60, "pence": 6000},
    {"pounds": 80, "pence": 8000},
    {"pounds": 100, "pence": 10000},
    {"pounds": 150, "pence": 15000},
    {"pounds": 200, "pence": 20000},
    {"pounds": 300, "pence": 30000},
]


@require_GET
def search(request):
    params = params_service.parse(request.GET, page_size=settings.SEARCH_PAGE_SIZE)

    near = (request.GET.get("near") or "").strip()
    location = None
    location_failed = False

    if near:
        # `geocode.resolve` is built not to raise — every HTTP failure inside it
        # returns None. This catches anyway, because the module's promise is that a
        # geocoder failure is never a search failure, and a promise enforced in one
        # place is one refactor away from being enforced nowhere. A bug in the
        # geocoder must not be able to take the search page down with it.
        try:
            location = geocode.resolve(near)
        except Exception:  # noqa: BLE001 — see above
            logger.exception("search.geocode_raised", extra={"near": near[:60]})
            location = None

        if location is None:
            # "We did not recognise that" and "we could not ask" look the same to
            # the visitor and need the same answer: results, and an honest note.
            location_failed = True
            # The OUTWARD code only. A full postcode identifies a household, and
            # this is the only place in Phase 4 that writes anything the visitor
            # typed to durable storage — while /privacy/ says there is "no
            # per-visitor record of what you looked at". "KT18" is enough to
            # diagnose a geocoding problem; "KT18 5EP" is somebody's street.
            logger.info("search.location_unresolved", extra={"near": near.split()[0][:4]})
        else:
            params.lat = location.lat
            params.lng = location.lng

    page = search_service.results_page(params)
    metrics.record_search_impressions(page.items, request=request)

    wider = search_service.wider_radius(params.radius_miles)
    counts = search_service.facet_counts(params)

    context = {
        "params": params,
        "page": page,
        "results": page.items,
        "near": near,
        "location": location,
        "location_failed": location_failed,
        "facets": params_service.facets_with_counts(counts, params),
        "facet_counts": counts,
        "active_filters": params_service.active_filters(params, request.GET),
        "wait_options": [
            {"value": value, "label": label} for value, label in params_service.WAIT_LABELS.items()
        ],
        "gender_options": Gender.choices,
        # TODO(sign-off): Dr. Abbass — verification-policy-derived public copy.
        # Narrowed to the control that is actually enforced: the DBS gate covers
        # under-18 CLIENT GROUPS, not every speciality that implies child work.
        # See the comment in templates/components/_filter_sidebar.html.
        "dbs_help": (
            "A practitioner's under-18 client groups are only shown once an enhanced "
            "DBS check has been verified."
        ),
        "experience_options": EXPERIENCE_OPTIONS,
        "fee_options": FEE_OPTIONS,
        "next_url": params_service.querystring_for_page(request.GET, page.page + 1),
        "previous_url": params_service.querystring_for_page(request.GET, page.page - 1),
        "wider_radius": wider,
        "wider_radius_url": (params_service.querystring_with(request.GET, "radius", wider) if wider else ""),
        "featured_cap": search_service.FEATURED_CAP_PER_PAGE,
    }

    if request.htmx:
        # The fragment the sidebar swaps into #results. Deliberately NOT a
        # separate URL: an HTMX-only endpoint is a second indexable surface
        # serving the same data with no chrome (docs/seo.md, "Crawlability").
        return render(request, "search/partials/_results.html", context)

    context.update(_seo(request, params))
    return render(request, "search/search.html", context)


@require_GET
def place_suggestions(request):
    """Options for the location field's ``<datalist>``.

    A native datalist rather than a hand-rolled combobox: the browser supplies the
    whole keyboard pattern — arrow keys, Escape, filtering, the announcement — and
    a datalist that never loads leaves a working text input behind. A
    ``<div>`` with a keydown handler is the thing this avoids.

    Returns markup rather than JSON because HTMX swaps HTML and because a JSON
    endpoint would be a second, undocumented API surface.

    **This response is head-less HTML, so it cannot carry a `noindex` meta tag.**
    An earlier version of this docstring argued that robots.txt did not need to
    disallow it "because it returns option elements with no links to follow", which
    applies the wrong test: the question is whether the URL is indexable, not
    whether it has outbound links, and Google indexes head-less HTML perfectly
    well. `?near=` is unbounded, so that would have been one thin URL per typed
    prefix — the exact "thin permutations at scale" failure the facet rule exists
    to prevent, on the one surface the facet rule does not cover.

    Three layers instead, cheapest first: `X-Robots-Tag` (the same header
    `directory.views._reveal_response` sets on the contact-reveal partial),
    `Disallow` in robots.txt, and a length cap so a crawler walking prefixes
    cannot drive unbounded outbound calls to postcodes.io.
    """
    query = (request.GET.get("near") or "").strip()[:60]
    response = render(
        request,
        "search/partials/_place_options.html",
        {"places": geocode.places(query) if query else []},
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _seo(request, params) -> dict:
    """Faceted URLs are ``noindex, follow`` with a canonical to the bare page.

    ``docs/seo.md`` is unambiguous: without this the facet engine generates
    millions of thin permutations and the subdomain reads as a doorway farm. The
    indexable surface is the curated landing pages, not this.
    """
    faceted = params_service.is_faceted(request.GET)

    # The title is STATIC and never echoes `q`.
    #
    # `meta_title` feeds both <title> and og:title, and a search for
    # "methylphenidate 27mg" therefore published
    # `<meta property="og:title" content="methylphenidate 27mg — search results">`
    # — a prescription-only medicine name in Kiam's own markup, in the field
    # WhatsApp and Slack read to build a link preview card.
    # docs/content-compliance.md §1 names meta titles explicitly, and this is
    # authored markup, so the submission lint never sees it. `noindex` limits
    # indexing, not the share card.
    #
    # The line this draws: a mechanical echo of the visitor's own input back to
    # them, so the search box shows what they typed, is how a search box works and
    # stays (the input `value` and the form's own hrefs). What Kiam ASSERTS about
    # the page to crawlers and to chat clients does not include anything a stranger
    # typed.
    # NOT the home page's title. `/` and `/search/` shipped byte-identical <title>,
    # og:title and <h1> — the two highest-priority indexable URLs on the subdomain,
    # both in the sitemap, asking to be told apart by a description alone
    # (docs/seo.md, "Every page"). Phase 5 made the duplication structural as well
    # as textual by giving the home page the same hero search bar and the same
    # disclaimer, so this is the half that moves: the home page keeps the phrase a
    # stranger actually searches for, and this page says what it is.
    title = "Search the directory"

    return {
        "meta_title": title,
        "meta_description": (
            "Search independent mental-health practitioners by speciality, approach, "
            "location and availability, and contact them directly."
        ),
        # `noindex, follow` on every faceted URL. The canonical is deliberately
        # NOT overridden: `templates/base.html` builds it from `request.path`,
        # which excludes the query string, so the default already points every
        # facet permutation at the bare `/search/`. Setting it here from
        # SITE_BASE_URL would say the same thing in a way that disagrees with
        # every other page on the site, and be wrong in any environment whose
        # host does not match that setting.
        "robots_content": "noindex, follow" if faceted else "",
        "jsonld": jsonld.search_results(path=request.path),
    }
