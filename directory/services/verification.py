"""
Verification state.

Two things are computed here and NOWHERE else:
  1. The Verified badge ("Credentials checked 12 July 2026") and its automatic lapse.
  2. minor_work_status — whether under-18 client groups may be shown.

Neither is a field an admin can toggle. Both derive from dated VerificationCheck rows,
so a lapsed insurance certificate or an expired DBS changes the public state without
anyone remembering to do anything.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from directory.models import (
    AuditLog,
    MinorWorkStatus,
    Practitioner,
    VerificationCheck,
    VerificationStatus,
    VerificationType,
)

# Checks every published practitioner must hold.
BASE_REQUIRED = [
    VerificationType.IDENTITY,
    VerificationType.REGISTRATION,
    VerificationType.QUALIFICATION,
    VerificationType.INSURANCE,
]

# How long a provisional (DBS-pending) listing may run before minor groups lock.
# 8 weeks covers a normal enhanced DBS turnaround.
PROVISIONAL_WINDOW_DAYS = 56

# Days before verification_expires_at at which to nudge.
REMINDER_DAYS = [60, 30, 7, 0]


def required_types(practitioner) -> list:
    types = list(BASE_REQUIRED)
    if practitioner.is_prescriber:
        types.append(VerificationType.PRESCRIBER)
    return types


@transaction.atomic
def recompute(practitioner: Practitioner) -> Practitioner:
    now = timezone.now()
    checks = {c.type: c for c in practitioner.verifications.all()}
    works_with_minors = practitioner.client_groups.filter(is_minors=True).exists()

    def is_live(check_type) -> bool:
        c = checks.get(check_type)
        if not c or c.status != VerificationStatus.VERIFIED:
            return False
        return not (c.expires_at and c.expires_at <= now)

    # --- 1. Badge ----------------------------------------------------------
    # The badge asserts only that the required documents were checked and remain in
    # date. It is not a competence or quality claim, and the public copy must not
    # imply that it is.
    required = required_types(practitioner)
    is_verified = all(is_live(t) for t in required)

    checked_dates = [checks[t].checked_at for t in required if t in checks and checks[t].checked_at]
    expiry_dates = [checks[t].expires_at for t in required if t in checks and checks[t].expires_at]

    # Show the OLDEST check date — the badge is only as fresh as its weakest element.
    credentials_checked_at = min(checked_dates) if (is_verified and checked_dates) else None
    verification_expires_at = min(expiry_dates) if expiry_dates else None

    # --- 2. Under-18 gating ------------------------------------------------
    minor_status = MinorWorkStatus.NOT_APPLICABLE
    provisional_expires_at = None

    if works_with_minors:
        dbs = checks.get(VerificationType.DBS)
        if dbs and dbs.status == VerificationStatus.VERIFIED and (not dbs.expires_at or dbs.expires_at > now):
            minor_status = MinorWorkStatus.CLEARED
        elif (
            dbs
            and dbs.provisional_until
            and dbs.provisional_until > now
            and dbs.status != VerificationStatus.REJECTED
        ):
            minor_status = MinorWorkStatus.PROVISIONAL
            provisional_expires_at = dbs.provisional_until
        else:
            # Missing, rejected, expired, or the provisional window ran out.
            minor_status = MinorWorkStatus.BLOCKED

    Practitioner.objects.filter(pk=practitioner.pk).update(
        is_verified=is_verified,
        credentials_checked_at=credentials_checked_at,
        verification_expires_at=verification_expires_at,
        minor_work_status=minor_status,
        provisional_expires_at=provisional_expires_at,
    )
    practitioner.refresh_from_db()
    return practitioner


@transaction.atomic
def grant_provisional_dbs(
    practitioner: Practitioner, actor, days: int = PROVISIONAL_WINDOW_DAYS, note: str = ""
):
    """
    Let a listing go live for ADULT WORK ONLY while an enhanced DBS is pending.

    Caller must have passed accounts.access.can_grant_provisional_dbs(). The window does
    not renew itself — extension is a separate deliberate act, and a second extension
    needs Dr. Abbass sign-off.
    """
    until = timezone.now() + timedelta(days=days)

    check, _ = VerificationCheck.objects.update_or_create(
        practitioner=practitioner,
        type=VerificationType.DBS,
        defaults={
            "status": VerificationStatus.IN_REVIEW,
            "provisional_granted_at": timezone.now(),
            "provisional_until": until,
            "provisional_granted_by": actor,
            "notes": note,
        },
    )

    AuditLog.objects.create(
        actor=actor,
        action="verification.dbs.provisional_granted",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={"provisional_until": until.isoformat(), "days": days},
    )
    return recompute(practitioner)


def nightly_sweep():
    """
    Management command, 03:00 daily. Lapses badges, locks expired provisional windows,
    returns the list of notifications to queue.
    """
    now = timezone.now()

    # 1. Expire checks past their expiry date.
    VerificationCheck.objects.filter(status=VerificationStatus.VERIFIED, expires_at__lte=now).update(
        status=VerificationStatus.EXPIRED
    )

    # 2. Recompute anyone whose badge or provisional window is affected.
    #
    # `+ 1` is load-bearing. `days_left` below is a truncating timedelta.days, so
    # the 60-day reminder needs an expiry between 60 and 61 days out — but a
    # horizon of exactly `max(REMINDER_DAYS)` excludes everything past 60 days
    # sharp. The widest reminder could therefore only ever fire on an
    # expiry that matched to the microsecond, i.e. never. One extra day of slack
    # brings the whole bucket inside the queryset; the `days_left in REMINDER_DAYS`
    # test below still decides who is actually notified.
    horizon = now + timedelta(days=max(REMINDER_DAYS) + 1)
    affected = Practitioner.objects.filter(status__in=["published", "approved"]).filter(
        models_Q_expiring(horizon, now)
    )

    notifications = []
    for practitioner in affected.prefetch_related("verifications", "client_groups"):
        before = practitioner.minor_work_status
        recompute(practitioner)

        if (
            before == MinorWorkStatus.PROVISIONAL
            and practitioner.minor_work_status == MinorWorkStatus.BLOCKED
        ):
            # The window ran out with no DBS. Minor groups are now hidden.
            # Notify the practitioner AND raise it in the admin queue — never silent.
            notifications.append({"practitioner": practitioner, "kind": "provisional_lapsed"})
            AuditLog.objects.create(
                action="verification.dbs.provisional_lapsed",
                entity_type="Practitioner",
                entity_id=str(practitioner.pk),
                after={"minor_work_status": practitioner.minor_work_status},
            )

        if practitioner.verification_expires_at:
            days_left = (practitioner.verification_expires_at - now).days
            if days_left in REMINDER_DAYS:
                notifications.append(
                    {"practitioner": practitioner, "kind": "expiry_reminder", "days_left": days_left}
                )

    return notifications


def models_Q_expiring(horizon, now):
    from django.db.models import Q

    return (
        Q(verification_expires_at__lte=horizon)
        | Q(provisional_expires_at__lte=now)
        | Q(minor_work_status=MinorWorkStatus.PROVISIONAL)
    )


# ===========================================================================
# Phase 2 additions
# ===========================================================================
# Everything above is the authored file. The two functions below were added for
# the verification workbench, and both write verification state — so they live
# here, next to recompute(), rather than in backoffice. Nothing outside this
# module is permitted to write the computed fields.

#: Types whose evidence carries an expiry date, per docs/verification-policy.md.
#: Marking one of these VERIFIED without an expiry would create a badge element
#: that can never lapse — which is the one thing the whole design is built to
#: prevent.
EXPIRING_TYPES = frozenset(
    {
        VerificationType.INSURANCE,
        VerificationType.DBS,
        VerificationType.ICO,
    }
)


class ExpiryRequired(ValueError):
    """A check of this type cannot be VERIFIED without an expiry date."""


@transaction.atomic
def set_check_status(check: VerificationCheck, *, status: str, actor, expires_at=None, notes: str = ""):
    """Record a verifier's decision on one piece of evidence, then recompute.

    The only supported way to move a check. It does NOT write is_verified or
    minor_work_status — it writes the evidence and calls recompute(), which
    derives them.
    """
    if status == VerificationStatus.VERIFIED and check.type in EXPIRING_TYPES and expires_at is None:
        raise ExpiryRequired(
            f"{check.get_type_display()} expires, so it needs an expiry date. "
            "Without one the badge could never lapse."
        )

    before = {
        "status": check.status,
        "expires_at": check.expires_at.isoformat() if check.expires_at else None,
    }

    check.status = status
    check.checked_by = actor
    check.checked_at = timezone.now() if status == VerificationStatus.VERIFIED else None
    if expires_at is not None:
        check.expires_at = expires_at
    if notes:
        check.notes = notes
    check.save(update_fields=["status", "checked_by", "checked_at", "expires_at", "notes"])

    AuditLog.objects.create(
        actor=actor,
        action=f"verification.{check.type}.{status}",
        entity_type="Practitioner",
        entity_id=str(check.practitioner_id),
        before=before,
        after={"status": status, "expires_at": check.expires_at.isoformat() if check.expires_at else None},
    )
    return recompute(check.practitioner)


#: A first extension is a judgement call a verifier can make. A second means the
#: DBS has been outstanding for roughly six months, and that is a clinical risk
#: decision, not an administrative one (docs/verification-policy.md).
MAX_SELF_SERVICE_EXTENSIONS = 1


class ExtensionRequiresSignOff(PermissionError):
    """A second extension needs Dr. Abbass sign-off recorded on the practitioner."""


@transaction.atomic
def extend_provisional_dbs(
    practitioner: Practitioner, actor, days: int = PROVISIONAL_WINDOW_DAYS, note: str = ""
):
    """Extend an open provisional window. Deliberate, counted, and capped.

    grant_provisional_dbs() opens the first window; this extends it. Separate
    functions on purpose — the window "does not self-renew", so extending has to
    be a thing someone chose to do and can be counted.
    """
    if practitioner.provisional_extensions >= MAX_SELF_SERVICE_EXTENSIONS:
        raise ExtensionRequiresSignOff(
            "This listing has already been extended once. A second extension needs "
            "Dr. Abbass to sign off on continuing to list for adult work while the "
            "DBS is outstanding."
        )

    check = practitioner.verifications.filter(type=VerificationType.DBS).first()
    if check is None or not check.provisional_until:
        raise ValueError("There is no provisional DBS window to extend.")

    until = timezone.now() + timedelta(days=days)
    check.provisional_until = until
    if note:
        check.notes = note
    check.save(update_fields=["provisional_until", "notes"])

    Practitioner.objects.filter(pk=practitioner.pk).update(
        provisional_extensions=practitioner.provisional_extensions + 1
    )
    practitioner.refresh_from_db(fields=["provisional_extensions"])

    AuditLog.objects.create(
        actor=actor,
        action="verification.dbs.provisional_extended",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={
            "provisional_until": until.isoformat(),
            "days": days,
            "extension_number": practitioner.provisional_extensions,
        },
    )
    return recompute(practitioner)
