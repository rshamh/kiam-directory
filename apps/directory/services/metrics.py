"""Daily counters.

``DailyMetric`` is counters and nothing else — no per-visitor rows, no session
identifier, no IP. That is what keeps it outside the cookie-consent burden and
out of scope for a subject access request, and it is why the increments live in
one small module instead of being scattered through views where somebody would
eventually "just add" a visitor id.

Increments are done with ``F()`` inside a queryset update, so two people opening
the same profile in the same second both count. A read-modify-write in Python
would lose one of them, and quietly — which is the worst possible failure for a
number that is meant to justify a subscription in year two.
"""

from __future__ import annotations

import logging

from django.db.models import F
from django.utils import timezone

from apps.directory.models import DailyMetric

logger = logging.getLogger("directory.metrics")

#: Reveal channel -> the column it counts. Keyed on the same three strings as
#: ``profile.CHANNELS``; ``test_metrics.py`` asserts the two never drift.
FIELD_BY_CHANNEL = {
    "email": "email_reveals",
    "phone": "phone_reveals",
    "website": "website_clicks",
}

#: Substrings that mark a request as a crawler rather than a person. Crude on
#: purpose: this is not bot *protection* (that is the reveal endpoint's job), it
#: only keeps the view count from being mostly Googlebot on day one. Anything
#: that lies about its user agent is counted, and that is an acceptable error in
#: a number nobody bills against.
CRAWLER_MARKERS = ("bot", "crawler", "spider", "slurp", "bingpreview", "headlesschrome")


def record_profile_view(practitioner, *, request=None) -> None:
    if request is not None and looks_automated(request):
        return
    _increment(practitioner, "profile_views")


def record_reveal(practitioner, channel: str) -> None:
    field = FIELD_BY_CHANNEL.get(channel)
    if field is None:
        # An unknown channel is a routing bug, not a metric to invent a column
        # for. Log it and count nothing.
        logger.warning("metrics.unknown_channel", extra={"channel": channel})
        return
    _increment(practitioner, field)


def record_search_impressions(practitioners, *, request=None) -> None:
    """Count one search appearance for everybody on this page of results.

    Two queries for a whole page, not two per practitioner. ``bulk_create`` with
    ``ignore_conflicts`` creates whichever rows are missing for today and silently
    skips the rest, then one ``update`` with ``F()`` increments them all — which
    also keeps the increment atomic, so two people running the same search in the
    same second both count.

    A per-practitioner ``get_or_create`` loop here would be forty queries on a page
    with a latency budget (docs/seo.md), for a counter.
    """
    if request is not None and looks_automated(request):
        return

    ids = [p.pk for p in practitioners]
    if not ids:
        return

    today = timezone.localdate()
    try:
        DailyMetric.objects.bulk_create(
            [DailyMetric(practitioner_id=pk, date=today) for pk in ids],
            ignore_conflicts=True,
        )
        DailyMetric.objects.filter(practitioner_id__in=ids, date=today).update(
            search_impressions=F("search_impressions") + 1
        )
    except Exception:  # noqa: BLE001 — a counter must never break a page
        logger.exception("metrics.impressions_failed", extra={"count": len(ids)})


def looks_automated(request) -> bool:
    agent = request.META.get("HTTP_USER_AGENT", "").lower()
    return any(marker in agent for marker in CRAWLER_MARKERS)


def _increment(practitioner, field: str) -> None:
    """Add one to today's row, creating it if this is the first hit of the day.

    ``get_or_create`` handles the race on the unique (practitioner, date)
    constraint itself — it catches the IntegrityError and re-reads — so two
    first-hits-of-the-day do not raise.

    A metric write must never be able to break a page. This is a counter; the
    profile it counts is the product.
    """
    try:
        metric, _ = DailyMetric.objects.get_or_create(practitioner=practitioner, date=timezone.localdate())
        DailyMetric.objects.filter(pk=metric.pk).update(**{field: F(field) + 1})
    except Exception:  # noqa: BLE001 — see the docstring
        logger.exception(
            "metrics.increment_failed",
            extra={"practitioner_id": str(practitioner.pk), "field": field},
        )
