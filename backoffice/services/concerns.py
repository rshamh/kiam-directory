"""Concern-report triage.

"Report a concern about this listing" — inaccurate details, a registration
doubt, misleading claims. The public form is Phase 3; this is the queue behind
it.

**Not a clinical complaints channel.** A complaint about someone's *care* goes to
their regulator, and the public page has to say so. What arrives here is about
the *listing*: whether what Kiam has published is accurate and whether the person
should still be on it. Staff who treat this queue as a complaints inbox will
mishandle both.

A concern about registration is the one that matters most — it is the early
warning for the struck-off-practitioner case that ``docs/verification-policy.md``
calls the reputational worst case. Those are surfaced first.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.access import can_review_submissions
from directory.models import AuditLog, ConcernReport

logger = logging.getLogger("backoffice.concerns")


class ConcernError(Exception):
    """The concern cannot be actioned."""


def open_queue():
    """Unhandled concerns. Registration doubts first, then oldest first.

    The ordering is a judgement: a registration concern may mean somebody
    currently listed should not be, and that is worth looking at before a typo in
    a phone number.
    """
    from django.db.models import Case, IntegerField, Value, When

    return (
        ConcernReport.objects.filter(handled_at__isnull=True)
        .annotate(
            priority=Case(
                When(category=ConcernReport.Category.NOT_REGISTERED, then=Value(0)),
                When(category=ConcernReport.Category.MISLEADING, then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .select_related("practitioner", "handled_by")
        .order_by("priority", "created_at")
    )


@transaction.atomic
def assign(concern: ConcernReport, *, actor, assignee=None):
    """Take ownership, so two people do not work the same report."""
    _require_staff(actor)

    concern.handled_by = assignee or actor
    concern.save(update_fields=["handled_by"])

    AuditLog.objects.create(
        actor=actor,
        action="concern.assigned",
        entity_type="ConcernReport",
        entity_id=str(concern.pk),
        after={"handled_by": concern.handled_by.email if concern.handled_by else None},
    )
    return concern


@transaction.atomic
def resolve(concern: ConcernReport, *, actor, outcome: str):
    """Close a concern with what was actually done about it.

    ``outcome`` is mandatory. A closed report with no outcome is indistinguishable
    from one nobody looked at, and this queue is part of how Kiam demonstrates it
    monitors its listings.
    """
    _require_staff(actor)

    if not (outcome or "").strip():
        raise ConcernError("Record what was done — an outcome is what makes this a record.")

    concern.outcome = outcome
    concern.handled_by = concern.handled_by or actor
    concern.handled_at = timezone.now()
    concern.save(update_fields=["outcome", "handled_by", "handled_at"])

    AuditLog.objects.create(
        actor=actor,
        action="concern.resolved",
        entity_type="ConcernReport",
        entity_id=str(concern.pk),
        after={"outcome": outcome, "practitioner_id": str(concern.practitioner_id)},
    )
    logger.info(
        "concerns.resolved",
        extra={"concern_id": str(concern.pk), "category": concern.category},
    )
    return concern


def _require_staff(actor) -> None:
    if not can_review_submissions(actor):
        raise PermissionDenied("This account cannot triage concern reports.")
