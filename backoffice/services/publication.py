"""Suspension and unpublication — taking a listing down, fast.

The reputational worst case for this project is a struck-off practitioner sitting
live on a Kiam-branded page (``docs/verification-policy.md``). So suspension is
one click, takes effect immediately, and does not wait for a cache to expire.

**A suspended profile must never explain itself.** It shows a neutral "not
currently listed" page or a 404 — never "suspended pending investigation". The
reason lives in the audit log where staff can see it. Publishing the reason would
be a defamation risk against a practitioner who may be cleared next week, and it
would tell the public something Kiam is not in a position to assert.

**Unpublish and suspend are different acts.** A practitioner withdrawing consent
unpublishes their own listing (Phase 6, one click, writes
``ConsentRecord.withdrawn_at``). Kiam suspending a listing is an enforcement
action by a named member of staff. Same visible outcome, different record, and
they must not be collapsed.
"""

from __future__ import annotations

import logging

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from accounts.access import can_suspend_listing
from directory.models import AuditLog, PublicationStatus

logger = logging.getLogger("backoffice.publication")

#: Cache keys that can hold a published listing. Busted on every state change,
#: because "takes effect within seconds" is the requirement and a stale home-page
#: grid is still a live listing as far as a visitor is concerned.
CACHE_KEY_PATTERNS = (
    "practitioner:{slug}",
    "profile:{slug}",
    "homepage:grid",
    # Phase 5. The home page's "by speciality" / "by town" links are counted from
    # published listings, so a suspension that empties a town has to remove its
    # link — otherwise the home page offers a browse route to nobody.
    "homepage:browse",
    "search:facets",
)


class SuspensionError(Exception):
    """The suspension cannot be applied."""


def bust_cache(practitioner) -> None:
    """Drop every cache entry that could still be serving this listing.

    Deliberately over-broad: the home-page grid and the facet counts are cheap to
    rebuild and expensive to get wrong. A suspended practitioner still appearing
    in a rotating grid for another hour is the failure this prevents.
    """
    cache.delete_many([pattern.format(slug=practitioner.slug) for pattern in CACHE_KEY_PATTERNS])


@transaction.atomic
def suspend(practitioner, *, actor, reason: str):
    """Pull a listing. Immediate, reasoned, audit-logged."""
    if not can_suspend_listing(actor):
        raise PermissionDenied("This account cannot suspend a listing.")

    if not (reason or "").strip():
        # Not a formality: this is the record that justifies the action if the
        # practitioner challenges it.
        raise SuspensionError("A suspension needs a reason on the record.")

    before = practitioner.status

    practitioner.status = PublicationStatus.SUSPENDED
    practitioner.suspended_at = timezone.now()
    practitioner.suspended_by = actor
    practitioner.suspend_reason = reason
    practitioner.save(update_fields=["status", "suspended_at", "suspended_by", "suspend_reason"])

    bust_cache(practitioner)

    AuditLog.objects.create(
        actor=actor,
        action="practitioner.suspended",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        before={"status": before},
        after={"status": practitioner.status, "reason": reason},
    )
    logger.warning(
        "publication.suspended",
        extra={"practitioner_id": str(practitioner.pk), "actor_id": actor.pk},
    )
    return practitioner


@transaction.atomic
def lift_suspension(practitioner, *, actor, notes: str = ""):
    """Restore a suspended listing to published."""
    if not can_suspend_listing(actor):
        raise PermissionDenied("This account cannot change a listing's suspension.")

    if practitioner.status != PublicationStatus.SUSPENDED:
        raise SuspensionError("This listing is not suspended.")

    # Re-derive the badge before going live. A suspension may have outlasted an
    # insurance certificate, and republishing must not restore a badge whose
    # evidence has since lapsed.
    from directory.services.verification import recompute

    recompute(practitioner)
    practitioner.refresh_from_db()

    # The same gate approve() applies. The reason suspension exists is the
    # struck-off practitioner: suspend -> registration unverified -> lift would
    # otherwise put a restricted title back online with nothing behind it.
    from .review import blocking_publication_reasons

    reasons = blocking_publication_reasons(practitioner)
    if reasons:
        raise SuspensionError("This listing cannot go back online yet. " + " ".join(reasons))

    practitioner.status = PublicationStatus.PUBLISHED
    practitioner.suspended_at = None
    practitioner.suspended_by = None
    practitioner.suspend_reason = ""
    practitioner.save(update_fields=["status", "suspended_at", "suspended_by", "suspend_reason"])

    bust_cache(practitioner)

    AuditLog.objects.create(
        actor=actor,
        action="practitioner.suspension_lifted",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={"status": practitioner.status, "notes": notes},
    )
    logger.info("publication.suspension_lifted", extra={"practitioner_id": str(practitioner.pk)})
    return practitioner


@transaction.atomic
def unpublish(practitioner, *, actor, reason: str = "", withdrawn_by_practitioner: bool = False):
    """Take a listing down without it being an enforcement action.

    ``withdrawn_by_practitioner`` marks the consent-withdrawal case, which is the
    practitioner exercising a right rather than Kiam acting. Phase 6's one-click
    dashboard button passes it and additionally writes
    ``ConsentRecord.withdrawn_at``; recording which it was matters because the two
    have different retention and different re-listing paths.
    """
    if not withdrawn_by_practitioner and not can_suspend_listing(actor):
        raise PermissionDenied("This account cannot unpublish a listing.")

    before = practitioner.status

    practitioner.status = PublicationStatus.UNPUBLISHED
    practitioner.save(update_fields=["status"])

    bust_cache(practitioner)

    AuditLog.objects.create(
        actor=actor,
        action="practitioner.unpublished",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        before={"status": before},
        after={
            "status": practitioner.status,
            "reason": reason,
            "withdrawn_by_practitioner": withdrawn_by_practitioner,
        },
    )
    logger.info(
        "publication.unpublished",
        extra={
            "practitioner_id": str(practitioner.pk),
            "withdrawn_by_practitioner": withdrawn_by_practitioner,
        },
    )
    return practitioner


def is_publicly_visible(practitioner) -> bool:
    """Whether the public profile should render at all.

    The single answer Phase 3's profile view asks. Anything that is not
    PUBLISHED shows the neutral not-listed page — including SUSPENDED, which
    must be indistinguishable from a listing that was never there.
    """
    return practitioner.status == PublicationStatus.PUBLISHED
