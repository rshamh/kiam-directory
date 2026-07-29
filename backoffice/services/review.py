"""Submission and review — the publication state machine.

    DRAFT ──submit──> SUBMITTED ──claim──> IN_REVIEW
                                              │
                        ┌─────────────────────┼─────────────────────┐
                     approve            request_changes          reject
                        │                     │                     │
                    PUBLISHED         CHANGES_REQUESTED          REMOVED

Three things here are load-bearing rather than plumbing.

**The snapshot.** ``ReviewRequest.snapshot`` is what was submitted, frozen at
submit time. A reviewer approves *that*, not whatever the practitioner has edited
since — otherwise the approval means nothing, because the copy could change
between the reviewer reading it and clicking approve.

**Approval cannot publish a restricted title without a verified registration.**
``docs/content-compliance.md`` §3 says "enforced at review, not just in the UI",
and this is where. The lint flags the claim at submission; this refuses to act on
it. Someone listing as a "Consultant Psychiatrist" with no verified GMC entry
does not go live because a reviewer was moving quickly.

**Approving an edit returns to PUBLISHED, not to a fresh publication.**
``published_at`` is set once. A practitioner who has been listed for a year and
changes their fees has not just been published.

Every transition writes an ``AuditLog`` row with the actor. There is no path
through this module that changes publication state without one.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.access import can_review_submissions
from directory.models import (
    AuditLog,
    PublicationStatus,
    ReviewOutcome,
    ReviewRequest,
)
from directory.services import lint

logger = logging.getLogger("backoffice.review")

#: Editing one of these on a published profile sends it back through review.
#: Everything else — bio, availability, photo, fees — publishes immediately
#: (docs/architecture.md, "Publication is a state machine").
CONTROLLED_FIELDS = (
    "full_name",
    "display_title",
    "post_nominals",
    "profession",
    "is_prescriber",
)

#: Statuses a reviewer can act on.
REVIEWABLE = (PublicationStatus.SUBMITTED, PublicationStatus.IN_REVIEW)


class SubmissionBlocked(Exception):
    """The lint refused this submission. Carries the findings."""

    def __init__(self, result: lint.LintResult):
        self.result = result
        super().__init__("; ".join(f.message for f in result.blocks))


class NotReviewable(Exception):
    """The profile is not in a state a reviewer can act on."""


def build_snapshot(practitioner) -> dict:
    """Exactly what is being submitted, in a form that survives later edits."""
    return {
        "full_name": practitioner.full_name,
        "display_title": practitioner.display_title,
        "post_nominals": practitioner.post_nominals,
        "pronouns": practitioner.pronouns,
        "profession": practitioner.profession.name if practitioner.profession else "",
        "intro": practitioner.intro,
        "services": practitioner.services,
        "availability_note": practitioner.availability_note,
        "public_email": practitioner.public_email,
        "public_phone": practitioner.public_phone,
        "public_website": practitioner.public_website,
        "booking_url": practitioner.booking_url,
        "delivery_mode": practitioner.delivery_mode,
        "offers_online": practitioner.offers_online,
        "online_coverage": practitioner.online_coverage,
        "is_prescriber": practitioner.is_prescriber,
        "fee_min": practitioner.fee_min,
        "fee_max": practitioner.fee_max,
        "specialities": sorted(practitioner.specialities.values_list("name", flat=True)),
        "approaches": sorted(practitioner.approaches.values_list("name", flat=True)),
        "client_groups": sorted(practitioner.client_groups.values_list("name", flat=True)),
        "languages": sorted(practitioner.languages.values_list("name", flat=True)),
        "locations": [
            {"city": loc.city, "postcode": loc.postcode, "is_public": loc.is_public}
            for loc in practitioner.locations.all()
        ],
        "captured_at": timezone.now().isoformat(),
    }


@transaction.atomic
def submit(practitioner, *, actor=None) -> ReviewRequest:
    """Lint, then queue for review. Raises ``SubmissionBlocked`` if the lint blocks.

    The lint runs BEFORE the state changes, so a blocked submission leaves the
    profile in DRAFT and the practitioner can fix it and try again.
    """
    result = lint.run(practitioner)

    if result.is_blocked:
        logger.info(
            "review.submission_blocked",
            extra={
                "practitioner_id": str(practitioner.pk),
                "rules": sorted({f.rule for f in result.blocks}),
            },
        )
        raise SubmissionBlocked(result)

    request = ReviewRequest.objects.create(
        practitioner=practitioner,
        snapshot=build_snapshot(practitioner),
        changed_fields=[],
        lint_flags=result.as_dict(),
    )

    practitioner.status = PublicationStatus.SUBMITTED
    practitioner.save(update_fields=["status"])

    AuditLog.objects.create(
        actor=actor or practitioner.user,
        action="practitioner.submitted",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={"review_request": str(request.pk), "held": result.must_hold_for_review},
    )
    logger.info(
        "review.submitted",
        extra={"practitioner_id": str(practitioner.pk), "held": result.must_hold_for_review},
    )
    return request


@transaction.atomic
def claim(request: ReviewRequest, *, actor) -> ReviewRequest:
    """Mark a submission as being looked at, so two reviewers do not collide."""
    _require_reviewer(actor)

    if request.practitioner.status != PublicationStatus.SUBMITTED:
        raise NotReviewable(f"Not awaiting review (status: {request.practitioner.status}).")

    request.practitioner.status = PublicationStatus.IN_REVIEW
    request.practitioner.save(update_fields=["status"])

    AuditLog.objects.create(
        actor=actor,
        action="review.claimed",
        entity_type="Practitioner",
        entity_id=str(request.practitioner_id),
        after={"review_request": str(request.pk)},
    )
    return request


def blocking_publication_reasons(practitioner) -> list[str]:
    """Why this profile may not be published yet. Empty means it may.

    Checked at approval rather than at submission because both conditions depend
    on verification, which happens after review.
    """
    reasons = []

    profession = practitioner.profession
    if profession and profession.restricted:
        required = set(profession.required_bodies or [])
        verified = set(practitioner.registrations.filter(verified=True).values_list("body", flat=True))
        if required and not (required & verified):
            reasons.append(
                f"“{profession.name}” is a restricted title. It needs a verified registration "
                f"with one of: {', '.join(sorted(required))}. "
                + (
                    f"Verified on file: {', '.join(sorted(verified))}."
                    if verified
                    else "No verified registration is on file."
                )
            )

    return reasons


@transaction.atomic
def approve(request: ReviewRequest, *, actor, notes: str = ""):
    """Publish. Refuses if publication is blocked (see the module docstring)."""
    _require_reviewer(actor)
    practitioner = request.practitioner
    _require_reviewable(practitioner)

    reasons = blocking_publication_reasons(practitioner)
    if reasons:
        logger.warning(
            "review.approve_refused",
            extra={"practitioner_id": str(practitioner.pk), "reasons": reasons},
        )
        raise NotReviewable(" ".join(reasons))

    was_published_before = practitioner.published_at is not None

    practitioner.status = PublicationStatus.PUBLISHED
    # Set once. Re-approving an edit does not make someone newly published.
    if not was_published_before:
        practitioner.published_at = timezone.now()
    practitioner.last_review_at = timezone.now()
    # Clear the whole suspension triple, not just two thirds of it — a live
    # listing carrying suspended_by reads as currently suspended to anyone
    # scanning the table.
    practitioner.suspended_at = None
    practitioner.suspended_by = None
    practitioner.suspend_reason = ""
    practitioner.save(
        update_fields=[
            "status",
            "published_at",
            "last_review_at",
            "suspended_at",
            "suspended_by",
            "suspend_reason",
        ]
    )

    _close(request, actor=actor, outcome=ReviewOutcome.APPROVED, notes=notes)

    AuditLog.objects.create(
        actor=actor,
        action="practitioner.published" if not was_published_before else "practitioner.republished",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        before={"status": PublicationStatus.IN_REVIEW},
        after={"status": practitioner.status},
    )
    logger.info(
        "review.approved",
        extra={"practitioner_id": str(practitioner.pk), "first_publication": not was_published_before},
    )
    return practitioner


@transaction.atomic
def request_changes(request: ReviewRequest, *, actor, notes: str):
    """Send it back. Notes are mandatory — "changes requested" with no reason is
    a dead end for the practitioner and a support ticket for us."""
    _require_reviewer(actor)
    if not (notes or "").strip():
        raise NotReviewable("Say what needs changing — the practitioner sees these notes.")

    practitioner = request.practitioner
    _require_reviewable(practitioner)

    practitioner.status = PublicationStatus.CHANGES_REQUESTED
    practitioner.last_review_at = timezone.now()
    practitioner.save(update_fields=["status", "last_review_at"])

    _close(request, actor=actor, outcome=ReviewOutcome.CHANGES_REQUESTED, notes=notes)

    AuditLog.objects.create(
        actor=actor,
        action="review.changes_requested",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={"notes": notes},
    )
    return practitioner


@transaction.atomic
def reject(request: ReviewRequest, *, actor, notes: str):
    """Refuse the listing outright."""
    _require_reviewer(actor)
    if not (notes or "").strip():
        raise NotReviewable("A rejection needs a reason on the record.")

    practitioner = request.practitioner
    _require_reviewable(practitioner)

    practitioner.status = PublicationStatus.REMOVED
    practitioner.last_review_at = timezone.now()
    practitioner.save(update_fields=["status", "last_review_at"])

    _close(request, actor=actor, outcome=ReviewOutcome.REJECTED, notes=notes)

    AuditLog.objects.create(
        actor=actor,
        action="review.rejected",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={"notes": notes},
    )
    return practitioner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_reviewer(actor) -> None:
    if not can_review_submissions(actor):
        raise PermissionDenied("This account cannot review submissions.")


def _require_reviewable(practitioner) -> None:
    if practitioner.status not in REVIEWABLE:
        raise NotReviewable(
            f"This profile is {practitioner.get_status_display().lower()}, not awaiting review."
        )


def _close(request: ReviewRequest, *, actor, outcome: str, notes: str) -> None:
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.outcome = outcome
    request.reviewer_notes = notes
    request.save(update_fields=["reviewed_by", "reviewed_at", "outcome", "reviewer_notes"])


def open_queue():
    """Submissions awaiting a decision, oldest first."""
    return (
        ReviewRequest.objects.filter(outcome="", practitioner__status__in=REVIEWABLE)
        .select_related("practitioner", "practitioner__profession")
        .order_by("submitted_at")
    )
