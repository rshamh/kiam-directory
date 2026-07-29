"""The nightly sweep, on a frozen clock.

Every behaviour here is about a date crossing a threshold while nobody is
watching, so the tests move the clock rather than the data. ``freeze_time`` wraps
both the setup and the sweep so "56 days later" means exactly that.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core import mail
from django.core.management import call_command
from django.utils import timezone
from freezegun import freeze_time

from directory.factories import PractitionerFactory
from directory.models import (
    AuditLog,
    MinorWorkStatus,
    PublicationStatus,
    VerificationCheck,
    VerificationStatus,
    VerificationType,
)
from directory.services import verification

pytestmark = pytest.mark.django_db

DAY_ONE = "2026-01-01 03:00:00"


def _published_verified(**kwargs):
    return PractitionerFactory(published=True, verified=True, **kwargs)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


@freeze_time(DAY_ONE)
def test_the_sweep_expires_a_check_that_has_passed_its_date():
    practitioner = _published_verified()
    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.INSURANCE).update(
        expires_at=timezone.now() - timedelta(days=1)
    )

    verification.nightly_sweep()

    check = VerificationCheck.objects.get(practitioner=practitioner, type=VerificationType.INSURANCE)
    assert check.status == VerificationStatus.EXPIRED


def test_an_expiring_certificate_lapses_the_badge_overnight():
    """No admin action anywhere in this test. That is the point."""
    with freeze_time(DAY_ONE) as clock:
        practitioner = _published_verified()
        VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.INSURANCE).update(
            expires_at=timezone.now() + timedelta(days=2)
        )
        verification.recompute(practitioner)
        assert practitioner.is_verified is True

        clock.move_to("2026-01-04 03:00:00")
        verification.nightly_sweep()

    practitioner.refresh_from_db()
    assert practitioner.is_verified is False


@freeze_time(DAY_ONE)
def test_a_healthy_practitioner_is_untouched():
    practitioner = _published_verified()

    verification.nightly_sweep()

    practitioner.refresh_from_db()
    assert practitioner.is_verified is True


# ---------------------------------------------------------------------------
# Provisional windows
# ---------------------------------------------------------------------------


def test_an_elapsed_provisional_window_blocks_and_notifies():
    """Never silent — email AND an admin-queue entry (verification-policy.md)."""
    with freeze_time(DAY_ONE):
        practitioner = PractitionerFactory(published=True, provisional_dbs=True)
        assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL

    # One day past the 56-day window.
    with freeze_time("2026-02-27 03:00:00"):
        notifications = verification.nightly_sweep()

    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED

    lapses = [n for n in notifications if n["kind"] == "provisional_lapsed"]
    assert len(lapses) == 1
    assert lapses[0]["practitioner"].pk == practitioner.pk

    assert AuditLog.objects.filter(action="verification.dbs.provisional_lapsed").exists()


def test_a_window_still_open_neither_blocks_nor_notifies():
    with freeze_time(DAY_ONE):
        practitioner = PractitionerFactory(published=True, provisional_dbs=True)

    # Halfway through the window.
    with freeze_time("2026-01-28 03:00:00"):
        notifications = verification.nightly_sweep()

    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL
    assert not [n for n in notifications if n["kind"] == "provisional_lapsed"]


def test_a_lapse_is_announced_once_not_every_night():
    """The second night it is already BLOCKED, so there is no transition."""
    with freeze_time(DAY_ONE):
        PractitionerFactory(published=True, provisional_dbs=True)

    with freeze_time("2026-02-27 03:00:00"):
        first = verification.nightly_sweep()
    with freeze_time("2026-02-28 03:00:00"):
        second = verification.nightly_sweep()

    assert len([n for n in first if n["kind"] == "provisional_lapsed"]) == 1
    assert not [n for n in second if n["kind"] == "provisional_lapsed"]


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days_out", [60, 30, 7])
def test_reminders_fire_at_the_policy_thresholds(days_out):
    with freeze_time(DAY_ONE):
        practitioner = _published_verified()
        expiry = timezone.now() + timedelta(days=days_out, hours=1)
        VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.INSURANCE).update(
            expires_at=expiry
        )
        verification.recompute(practitioner)

        notifications = verification.nightly_sweep()

    reminders = [n for n in notifications if n["kind"] == "expiry_reminder"]
    assert len(reminders) == 1
    assert reminders[0]["days_left"] == days_out


@freeze_time(DAY_ONE)
def test_no_reminder_on_a_day_that_is_not_a_threshold():
    practitioner = _published_verified()
    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.INSURANCE).update(
        expires_at=timezone.now() + timedelta(days=45, hours=1)
    )
    verification.recompute(practitioner)

    notifications = verification.nightly_sweep()

    assert not [n for n in notifications if n["kind"] == "expiry_reminder"]


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@freeze_time(DAY_ONE)
def test_a_draft_listing_is_not_swept():
    """Only published and approved listings have a public state to protect."""
    practitioner = PractitionerFactory(verified=True)
    assert practitioner.status == PublicationStatus.DRAFT

    VerificationCheck.objects.filter(practitioner=practitioner, type=VerificationType.INSURANCE).update(
        expires_at=timezone.now() + timedelta(days=30, hours=1)
    )
    verification.recompute(practitioner)

    notifications = verification.nightly_sweep()

    assert not notifications


# ---------------------------------------------------------------------------
# The management command
# ---------------------------------------------------------------------------


def test_the_command_emails_a_lapsed_practitioner(user_factory):
    user = user_factory("listed@example.com")

    with freeze_time(DAY_ONE):
        PractitionerFactory(published=True, provisional_dbs=True, user=user)

    mail.outbox.clear()
    with freeze_time("2026-02-27 03:00:00"):
        call_command("verification_sweep", verbosity=0)

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["listed@example.com"]
    assert "paused" in message.subject
    assert "adult work" in message.body


def test_the_command_sends_to_the_account_email_not_the_published_one(user_factory):
    """`public_email` may be a receptionist. Verification state is not their business."""
    user = user_factory("owner@example.com")

    with freeze_time(DAY_ONE):
        PractitionerFactory(
            published=True,
            provisional_dbs=True,
            user=user,
            public_email="reception@clinic.example",
        )

    mail.outbox.clear()
    with freeze_time("2026-02-27 03:00:00"):
        call_command("verification_sweep", verbosity=0)

    assert mail.outbox[0].to == ["owner@example.com"]


def test_the_command_sends_nothing_on_a_quiet_night():
    with freeze_time(DAY_ONE):
        _published_verified()
        mail.outbox.clear()
        call_command("verification_sweep", verbosity=0)

    assert mail.outbox == []


def test_dry_run_recomputes_but_sends_no_email(user_factory):
    user = user_factory("listed@example.com")

    with freeze_time(DAY_ONE):
        practitioner = PractitionerFactory(published=True, provisional_dbs=True, user=user)

    mail.outbox.clear()
    with freeze_time("2026-02-27 03:00:00"):
        call_command("verification_sweep", "--dry-run", verbosity=0)

    assert mail.outbox == []
    practitioner.refresh_from_db()
    # The recompute still happened — only sending was skipped.
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED


def test_a_practitioner_with_no_account_does_not_break_the_sweep():
    """A listing created by admin before the invite is accepted has no user."""
    with freeze_time(DAY_ONE):
        practitioner = PractitionerFactory(published=True, provisional_dbs=True)
        assert practitioner.user is None

    mail.outbox.clear()
    with freeze_time("2026-02-27 03:00:00"):
        call_command("verification_sweep", verbosity=0)

    assert mail.outbox == []
    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED


def test_one_failing_send_does_not_stop_the_others(user_factory, monkeypatch):
    """A full mailbox on one account must not silence ninety-nine others."""
    good = user_factory("good@example.com")
    bad = user_factory("bad@example.com")

    with freeze_time(DAY_ONE):
        PractitionerFactory(published=True, provisional_dbs=True, user=bad)
        PractitionerFactory(published=True, provisional_dbs=True, user=good)

    calls = []
    real_send_mail = __import__(
        "directory.management.commands.verification_sweep", fromlist=["send_mail"]
    ).send_mail

    def flaky(*args, **kwargs):
        recipients = kwargs.get("recipient_list") or []
        calls.append(recipients)
        if "bad@example.com" in recipients:
            raise RuntimeError("mailbox full")
        return real_send_mail(*args, **kwargs)

    monkeypatch.setattr("directory.management.commands.verification_sweep.send_mail", flaky)

    mail.outbox.clear()
    with freeze_time("2026-02-27 03:00:00"):
        call_command("verification_sweep", verbosity=0)

    assert len(calls) == 2, "The sweep stopped at the first failure."
    assert mail.outbox[0].to == ["good@example.com"]


def test_the_sweep_logs_what_it_sent(user_factory, caplog):
    import logging

    user = user_factory("listed@example.com")
    with freeze_time(DAY_ONE):
        PractitionerFactory(published=True, provisional_dbs=True, user=user)

    with freeze_time("2026-02-27 03:00:00"), caplog.at_level(logging.INFO, logger="directory.verification"):
        call_command("verification_sweep", verbosity=0)

    messages = [r.getMessage() for r in caplog.records]
    assert "verification.notification" in messages
    assert "verification.sweep_complete" in messages
