"""The verification service.

This is the most consequential file in the project. It decides whether a badge
appears next to a named clinician's photograph, and whether a practitioner whose
DBS has not come back can be found by someone searching for a therapist for their
child. Both answers have to come from dated evidence and nothing else.

Every test here drives the service the way production does — create dated
``VerificationCheck`` rows, call ``recompute()`` — and never writes a
verification field directly. A test that did would be asserting against a state
the real code could not produce.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.directory.factories import (
    ClientGroupFactory,
    PractitionerFactory,
    VerificationCheckFactory,
)
from apps.directory.models import (
    MinorWorkStatus,
    VerificationCheck,
    VerificationStatus,
    VerificationType,
)
from apps.directory.services import verification

pytestmark = pytest.mark.django_db


def _verify_all(practitioner, *, checked_at=None, insurance_expires=None):
    """Give a practitioner every check their profile requires, all live."""
    now = timezone.now()
    for check_type in verification.required_types(practitioner):
        VerificationCheckFactory(
            practitioner=practitioner,
            type=check_type,
            status=VerificationStatus.VERIFIED,
            checked_at=checked_at or now,
            expires_at=insurance_expires if check_type == VerificationType.INSURANCE else None,
        )
    return practitioner


# ---------------------------------------------------------------------------
# The badge
# ---------------------------------------------------------------------------


def test_no_badge_without_any_evidence():
    practitioner = PractitionerFactory()
    verification.recompute(practitioner)

    assert practitioner.is_verified is False
    assert practitioner.credentials_checked_at is None


def test_badge_requires_every_required_check():
    """One missing check is enough to withhold the badge."""
    practitioner = PractitionerFactory()
    required = verification.required_types(practitioner)

    # All but the last.
    for check_type in required[:-1]:
        VerificationCheckFactory(practitioner=practitioner, type=check_type)
    verification.recompute(practitioner)
    assert practitioner.is_verified is False

    VerificationCheckFactory(practitioner=practitioner, type=required[-1])
    verification.recompute(practitioner)
    assert practitioner.is_verified is True


@pytest.mark.parametrize(
    "status",
    [
        VerificationStatus.NOT_SUBMITTED,
        VerificationStatus.SUBMITTED,
        VerificationStatus.IN_REVIEW,
        VerificationStatus.REJECTED,
        VerificationStatus.EXPIRED,
    ],
)
def test_only_verified_status_counts(status):
    """Submitted is not verified. In review is not verified."""
    practitioner = PractitionerFactory()
    _verify_all(practitioner)

    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.IDENTITY).update(
        status=status
    )
    verification.recompute(practitioner)

    assert practitioner.is_verified is False


def test_credentials_checked_at_is_the_oldest_check_not_the_newest():
    """The badge is only as fresh as its weakest element (verification-policy.md).

    Showing the newest date would let a practitioner refresh one cheap document
    and present a five-year-old identity check as current.
    """
    practitioner = PractitionerFactory()
    now = timezone.now()
    required = verification.required_types(practitioner)

    oldest = now - timedelta(days=400)
    for offset, check_type in enumerate(required):
        VerificationCheckFactory(
            practitioner=practitioner,
            type=check_type,
            status=VerificationStatus.VERIFIED,
            checked_at=now - timedelta(days=400 - offset * 100),
        )

    verification.recompute(practitioner)

    assert practitioner.is_verified is True
    assert practitioner.credentials_checked_at == oldest
    assert practitioner.credentials_checked_at != now


def test_verification_expires_at_is_the_earliest_expiry():
    practitioner = PractitionerFactory()
    now = timezone.now()

    VerificationCheckFactory(
        practitioner=practitioner,
        type=VerificationType.INSURANCE,
        expires_at=now + timedelta(days=30),
    )
    VerificationCheckFactory(
        practitioner=practitioner,
        type=VerificationType.REGISTRATION,
        expires_at=now + timedelta(days=200),
    )
    verification.recompute(practitioner)

    assert practitioner.verification_expires_at == now + timedelta(days=30)


def test_expiring_insurance_lapses_the_badge_with_no_human_action():
    """The core promise of computing this: nobody has to remember.

    An indemnity certificate that runs out on a Sunday night takes the badge with
    it, without an admin noticing, a queue item, or anyone logging in.
    """
    practitioner = PractitionerFactory()
    now = timezone.now()
    _verify_all(practitioner, insurance_expires=now + timedelta(days=1))

    verification.recompute(practitioner)
    assert practitioner.is_verified is True

    # The certificate lapses. Nothing else happens — no status change, no admin.
    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.INSURANCE).update(
        expires_at=now - timedelta(seconds=1)
    )

    verification.recompute(practitioner)
    assert practitioner.is_verified is False
    assert practitioner.credentials_checked_at is None


def test_a_check_with_no_expiry_never_lapses():
    """Photo ID and qualifications do not expire (verification-policy.md)."""
    practitioner = PractitionerFactory()
    _verify_all(practitioner, insurance_expires=None)

    verification.recompute(practitioner)
    assert practitioner.is_verified is True


def test_is_prescriber_adds_prescriber_to_the_required_set():
    practitioner = PractitionerFactory(is_prescriber=True)

    assert VerificationType.PRESCRIBER in verification.required_types(practitioner)

    # Everything except the prescriber check.
    for check_type in verification.BASE_REQUIRED:
        VerificationCheckFactory(practitioner=practitioner, type=check_type)
    verification.recompute(practitioner)
    assert practitioner.is_verified is False

    VerificationCheckFactory(practitioner=practitioner, type=VerificationType.PRESCRIBER)
    verification.recompute(practitioner)
    assert practitioner.is_verified is True


def test_a_non_prescriber_does_not_need_the_prescriber_check():
    practitioner = PractitionerFactory(is_prescriber=False)

    assert VerificationType.PRESCRIBER not in verification.required_types(practitioner)

    _verify_all(practitioner)
    verification.recompute(practitioner)
    assert practitioner.is_verified is True


def test_an_unrelated_check_does_not_earn_a_badge():
    """A DBS on file is not a substitute for insurance."""
    practitioner = PractitionerFactory()
    VerificationCheckFactory(practitioner=practitioner, type=VerificationType.DBS)

    verification.recompute(practitioner)
    assert practitioner.is_verified is False


# ---------------------------------------------------------------------------
# Under-18 gating — all four states
# ---------------------------------------------------------------------------


def test_minor_status_not_applicable_without_minor_client_groups():
    practitioner = PractitionerFactory()
    practitioner.client_groups.set([ClientGroupFactory(slug="adults", name="Adults (18+)")])

    verification.recompute(practitioner)

    assert practitioner.minor_work_status == MinorWorkStatus.NOT_APPLICABLE
    assert practitioner.can_show_minor_groups is False


def test_minor_status_blocked_when_a_minor_group_has_no_dbs():
    """Selecting an under-18 group with no DBS locks it — it does not just idle."""
    practitioner = PractitionerFactory()
    practitioner.client_groups.set([ClientGroupFactory(minors=True)])

    verification.recompute(practitioner)

    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED
    assert practitioner.can_show_minor_groups is False


def test_minor_status_provisional_while_a_dbs_window_is_open():
    practitioner = PractitionerFactory(provisional_dbs=True)

    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL
    assert practitioner.provisional_expires_at is not None
    # PROVISIONAL is live for ADULT WORK ONLY — minor groups stay hidden.
    assert practitioner.can_show_minor_groups is False


def test_minor_status_cleared_with_a_verified_in_date_dbs():
    practitioner = PractitionerFactory(dbs_cleared=True)

    assert practitioner.minor_work_status == MinorWorkStatus.CLEARED
    assert practitioner.can_show_minor_groups is True


def test_a_cleared_practitioner_shows_their_minor_groups():
    practitioner = PractitionerFactory(dbs_cleared=True)
    assert practitioner.visible_client_groups().filter(is_minors=True).exists()


def test_a_provisional_practitioner_hides_their_minor_groups():
    """The profile half of the two-place gate (CLAUDE.md)."""
    practitioner = PractitionerFactory(provisional_dbs=True)

    assert practitioner.client_groups.filter(is_minors=True).exists()
    assert not practitioner.visible_client_groups().filter(is_minors=True).exists()


def test_an_expired_dbs_blocks_rather_than_downgrading_to_provisional():
    practitioner = PractitionerFactory(dbs_cleared=True)
    assert practitioner.minor_work_status == MinorWorkStatus.CLEARED

    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.DBS).update(
        expires_at=timezone.now() - timedelta(days=1)
    )
    verification.recompute(practitioner)

    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED


def test_a_rejected_dbs_blocks_even_inside_an_open_window():
    """A rejection is not something a provisional window should paper over."""
    practitioner = PractitionerFactory(provisional_dbs=True)
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL

    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.DBS).update(
        status=VerificationStatus.REJECTED
    )
    verification.recompute(practitioner)

    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED


def test_an_elapsed_provisional_window_blocks():
    practitioner = PractitionerFactory(provisional_dbs=True)

    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.DBS).update(
        provisional_until=timezone.now() - timedelta(seconds=1)
    )
    verification.recompute(practitioner)

    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED
    assert practitioner.provisional_expires_at is None


def test_minor_gating_is_independent_of_the_badge():
    """A fully verified practitioner with no DBS is still blocked for minors."""
    practitioner = PractitionerFactory()
    practitioner.client_groups.set([ClientGroupFactory(minors=True)])
    _verify_all(practitioner)
    verification.recompute(practitioner)

    assert practitioner.is_verified is True
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED


# ---------------------------------------------------------------------------
# Granting a provisional window
# ---------------------------------------------------------------------------


def test_grant_provisional_dbs_opens_a_window_and_audits_it(verifier):
    from apps.directory.models import AuditLog

    practitioner = PractitionerFactory()
    practitioner.client_groups.set([ClientGroupFactory(minors=True)])

    verification.grant_provisional_dbs(practitioner, actor=verifier, note="DBS submitted 3 Jul")

    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL

    entry = AuditLog.objects.get(action="verification.dbs.provisional_granted")
    assert entry.actor == verifier
    assert entry.entity_id == str(practitioner.pk)


def test_the_provisional_window_is_56_days(verifier):
    practitioner = PractitionerFactory()
    practitioner.client_groups.set([ClientGroupFactory(minors=True)])

    verification.grant_provisional_dbs(practitioner, actor=verifier)
    practitioner.refresh_from_db()

    days = (practitioner.provisional_expires_at - timezone.now()).days
    assert days == verification.PROVISIONAL_WINDOW_DAYS - 1  # partial day floors
    assert verification.PROVISIONAL_WINDOW_DAYS == 56


def test_the_window_does_not_self_renew(verifier):
    """Extension is a separate deliberate act (verification-policy.md)."""
    practitioner = PractitionerFactory()
    practitioner.client_groups.set([ClientGroupFactory(minors=True)])
    verification.grant_provisional_dbs(practitioner, actor=verifier)

    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.DBS).update(
        provisional_until=timezone.now() - timedelta(days=1)
    )
    verification.recompute(practitioner)

    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED


# ---------------------------------------------------------------------------
# recompute() is the only writer
# ---------------------------------------------------------------------------


def test_recompute_is_idempotent():
    practitioner = PractitionerFactory(verified=True)
    before = (
        practitioner.is_verified,
        practitioner.credentials_checked_at,
        practitioner.verification_expires_at,
        practitioner.minor_work_status,
    )

    verification.recompute(practitioner)
    after = (
        practitioner.is_verified,
        practitioner.credentials_checked_at,
        practitioner.verification_expires_at,
        practitioner.minor_work_status,
    )

    assert before == after


def test_recompute_overwrites_a_hand_set_badge():
    """If someone reaches into the ORM, the next recompute corrects it.

    This is what "computed, never toggled" buys: the value is derived, so a
    manual write is transient rather than sticky.
    """
    practitioner = PractitionerFactory()
    type(practitioner).objects.filter(pk=practitioner.pk).update(
        is_verified=True, minor_work_status=MinorWorkStatus.CLEARED
    )
    practitioner.refresh_from_db()
    assert practitioner.is_verified is True

    verification.recompute(practitioner)

    assert practitioner.is_verified is False
    assert practitioner.minor_work_status == MinorWorkStatus.NOT_APPLICABLE
