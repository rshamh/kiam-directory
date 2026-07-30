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
from datetime import timedelta

from django.conf import settings
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


# ===========================================================================
# Phase 6 additions — the practitioner takes their own listing down
# ===========================================================================
# `unpublish()` above already carries `withdrawn_by_practitioner`, and its
# docstring says the Phase 6 dashboard "additionally writes
# ConsentRecord.withdrawn_at". These two functions are that, and they are
# functions rather than two lines in a view for one reason: consent is the lawful
# basis for publishing this data, so an unpublish that forgets to record the
# withdrawal is a GDPR failure that leaves the record saying the practitioner
# still consents. Putting both writes inside one atomic function means they
# cannot come apart.
#
# The brief: "withdrawal must be as easy as giving it. A single clear action,
# confirmation, immediate effect... No 'contact us to be removed' anywhere."


class ConsentError(Exception):
    """The withdrawal cannot be applied."""


@transaction.atomic
def withdraw_consent(practitioner, *, actor, reason: str = ""):
    """The practitioner withdraws consent. Listing down, consent record closed.

    Not an enforcement action and not recorded as one: `unpublish()` is called
    with `withdrawn_by_practitioner=True`, which is what distinguishes this in the
    audit log from Kiam pulling a listing. Same visible outcome, different record,
    different re-listing path — which is why `publication.unpublish` tracks the
    difference at all.

    **Every open consent row is closed, not just the newest.** A practitioner who
    re-consented after a terms change has more than one, and leaving an earlier row
    open would leave a live "yes" on the record after an explicit "no".

    Idempotent. Pressing the button twice, or on an already-unpublished listing,
    is not an error — it is somebody making sure.
    """
    withdrawn_at = timezone.now()

    closed = practitioner.consents.filter(withdrawn_at__isnull=True).update(withdrawn_at=withdrawn_at)

    if practitioner.status == PublicationStatus.PUBLISHED:
        unpublish(
            practitioner,
            actor=actor,
            reason=reason,
            withdrawn_by_practitioner=True,
        )
    else:
        # Already down. Still record the withdrawal — the consent record and the
        # publication status answer different questions, and a draft listing whose
        # owner has said "no" must not be publishable by a reviewer tomorrow.
        practitioner.status = PublicationStatus.UNPUBLISHED
        practitioner.save(update_fields=["status"])
        bust_cache(practitioner)

    AuditLog.objects.create(
        actor=actor,
        action="consent.withdrawn",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={
            "withdrawn_at": withdrawn_at.isoformat(),
            "consent_records_closed": closed,
            "reason": reason,
        },
    )
    logger.info(
        "publication.consent_withdrawn",
        extra={"practitioner_id": str(practitioner.pk), "consent_records_closed": closed},
    )
    return practitioner


@transaction.atomic
def request_removal(practitioner, *, actor, reason: str = ""):
    """Full removal: not listed, and not held for re-publication.

    Stronger than `withdraw_consent()` and offered separately because they are
    different asks. Withdrawal is "take my page down"; removal is "and stop
    holding my evidence". Collapsing them would mean either that a practitioner
    taking a break for a month loses their documents, or that somebody who wants
    to be gone stays on file.

    Sets `Document.delete_after` on every evidence file, which is what the nightly
    job acts on. It does **not** delete anything here: `docs/verification-policy.md`
    says the retention period balances due-diligence defence against data
    minimisation and is "a solicitor question — do not pick a number in code
    without that answer; leave it configurable". `EVIDENCE_RETENTION_DAYS` is that
    setting, and it carries a TODO(sign-off) rather than a confident number.

    Consent is withdrawn as part of this. Removal without withdrawal would leave a
    consent record saying yes on an account that has asked to be erased.
    """
    withdraw_consent(practitioner, actor=actor, reason=reason)

    delete_after = timezone.now() + timedelta(days=settings.EVIDENCE_RETENTION_DAYS)
    scheduled = practitioner.documents.filter(delete_after__isnull=True).update(delete_after=delete_after)

    practitioner.status = PublicationStatus.REMOVED
    practitioner.save(update_fields=["status"])
    bust_cache(practitioner)

    AuditLog.objects.create(
        actor=actor,
        action="practitioner.removal_requested",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={
            "documents_scheduled": scheduled,
            "delete_after": delete_after.isoformat(),
            "retention_days": settings.EVIDENCE_RETENTION_DAYS,
            "reason": reason,
        },
    )
    logger.warning(
        "publication.removal_requested",
        extra={
            "practitioner_id": str(practitioner.pk),
            "documents_scheduled": scheduled,
            "retention_days": settings.EVIDENCE_RETENTION_DAYS,
        },
    )
    return practitioner
