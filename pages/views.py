"""Public content pages.

The home page is Phase 5: a hero search that submits to ``/search/``, a
daily-rotating grid of twelve, the independence notice above the grid, and browse
entry points. The work is in ``pages/services/home.py`` — the view assembles a
context and renders.

Everything else here is Phase 3's static set. They share one view: the registry
in ``pages/content.py`` holds each page's URL, title, meta description and
template, so a new page cannot be added without a description and cannot be
added without joining the sitemap.

``/report-a-concern/`` is the exception. It takes a POST and writes a row, so it
has a view of its own.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods

from accounts.services import ratelimit
from backoffice.services import concerns as concerns_service
from directory.services import search as search_service
from seo import jsonld

from . import content, forms
from .services import home as home_service

logger = logging.getLogger("pages")


@require_GET
def home(request):
    """The home page.

    Nothing here is per-visitor, which is what lets the grid and the browse links
    be cached (``pages.services.home``). The view holds no cache logic of its own
    and reads nothing off ``request`` except to render — so there is no path by
    which something user-specific could end up in a shared cache entry.

    **No search impression is recorded for the grid.** ``metrics`` counts
    ``search_impressions``, which means "appeared in a set of search results";
    the grid is a rotating sample of the directory, not an answer to a query, and
    counting it as one would inflate the number Phase 6's insights page shows a
    practitioner and Phase 7 uses to justify a subscription. It is also behind a
    24-hour cache, so a per-request counter here would count the request and not
    the selection. Carried forward as its own counter if the dashboard needs it.
    """
    return render(
        request,
        "pages/home.html",
        {
            "meta_title": "Find an independent mental-health practitioner",
            "meta_description": (
                "A directory of independent mental-health practitioners, published by "
                "Kiam Clinic. Search by speciality and location, and contact "
                "practitioners directly."
            ),
            "jsonld": jsonld.home(),
            **home_service.hero(),
            "grid": home_service.grid(),
            "browse": home_service.browse_entry_points(),
            "featured_cap": search_service.FEATURED_CAP_PER_PAGE,
        },
    )


@require_GET
def static_page(request, page_name: str):
    page = content.BY_NAME.get(page_name)
    if page is None:  # pragma: no cover — only reachable from a broken urls.py
        raise Http404("No such page.")

    path = reverse(f"pages:{page.name}")
    return render(
        request,
        page.template,
        {
            "page": page,
            "meta_title": page.title,
            "meta_description": page.description,
            "jsonld": jsonld.static_page(path=path, name=page.title, description=page.description),
        },
    )


@require_http_methods(["GET", "POST"])
def report_concern(request):
    """Report a concern about a listing.

    Three things this page has to get right, in order of how badly they go wrong:

    1. **It is not a clinical complaints channel.** A complaint about someone's
       care goes to their regulator, and the page names them. Kiam is not in a
       position to investigate care it neither provided nor supervised, and a
       page that implied otherwise would collect complaints that then went
       nowhere.
    2. **A concern must be reportable without an account and without a name.**
       The email address is optional and the copy says the report is still read.
    3. **Spam protection without a puzzle.** Honeypot plus a per-IP limit on
       *writes*. No CAPTCHA — see the comment in pages/forms.py.

    The success state renders in place rather than redirecting, and it changes
    the page's ``<h1>`` and ``<title>`` rather than adding a status box. This
    form does not swap — it is a plain POST — so a ``role="status"`` that is
    already in the DOM when the response arrives is announced by nothing: live
    regions only fire on mutation after they have been registered. A changed
    document title is what a screen reader announces on navigation, so that is
    where the confirmation lives.

    A refresh can re-post; a duplicate row in a small triage queue is a smaller
    cost than a confirmation nobody hears.
    """
    submitted = False
    form = forms.ConcernForm(request.POST or None, initial={"listing": request.GET.get("listing", "")})

    if request.method == "POST" and form.is_valid():
        if form.is_bot:
            # Answer exactly as a success would, and write nothing. Telling a bot
            # it was caught only teaches it which field to leave alone.
            logger.info("concerns.honeypot_tripped")
            submitted = True
        elif _rate_limited(request):
            form.add_error(
                None,
                "We've had several reports from your connection in the last hour. "
                "Please try again later, or email info@kiamclinic.com.",
            )
        else:
            concerns_service.submit(
                practitioner=form.practitioner,
                category=form.cleaned_data["category"],
                detail=form.cleaned_data["detail"],
                reporter_email=form.cleaned_data["reporter_email"],
            )
            submitted = True

    path = reverse("pages:report_concern")
    page_name = "Report a concern about a listing"
    description = (
        "Tell us if something in a Kiam Clinic Directory listing is wrong or "
        "misleading. Concerns about a practitioner's care go to their regulator — "
        "this page names them."
    )
    return render(
        request,
        "pages/report_concern.html",
        {
            "form": form,
            "submitted": submitted,
            # The <title> carries the confirmation, because a changed document
            # title is what a screen reader announces on navigation — see the
            # docstring. The JSON-LD keeps the page's stable identity: what this
            # URL *is* does not change because somebody just posted to it.
            "meta_title": ("Thank you — your report has been received" if submitted else page_name),
            "meta_description": description,
            "jsonld": jsonld.static_page(path=path, name=page_name, description=description),
        },
    )


def _rate_limited(request) -> bool:
    ip = ratelimit.client_ip(request)
    if not ip:
        return False
    result = ratelimit.hit(
        "concern_report",
        ip,
        limit=settings.CONCERN_REPORT_MAX_PER_IP,
        window_seconds=settings.CONCERN_REPORT_WINDOW_SECONDS,
    )
    return result.exceeded
