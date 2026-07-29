"""Invite → draft → submit → lint → verify → publish, end to end.

The gate for this phase. Every step goes through the real service, and the
assertions are about what a member of the public would be able to see.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.utils import timezone

from accounts.models import Invite, User
from backoffice.services import invites, publication, review
from directory.models import (
    AuditLog,
    MinorWorkStatus,
    Practitioner,
    PublicationStatus,
    Registration,
    VerificationStatus,
    VerificationType,
)
from directory.services import verification

pytestmark = pytest.mark.django_db


def _verify_everything(practitioner, verifier_user):
    """Walk the required checks through the workbench service, as a verifier would."""
    for check_type in verification.required_types(practitioner):
        check, _ = practitioner.verifications.get_or_create(type=check_type)
        verification.set_check_status(
            check,
            status=VerificationStatus.VERIFIED,
            actor=verifier_user,
            expires_at=(
                timezone.now() + timezone.timedelta(days=365)
                if check_type in verification.EXPIRING_TYPES
                else None
            ),
        )
    practitioner.refresh_from_db()
    return practitioner


def test_invite_to_published_end_to_end(admin_user, verifier):
    # --- 1. Admin invites -------------------------------------------------
    invite = invites.issue_and_send("newdoc@example.com", invited_by=admin_user, note="Met at conf")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["newdoc@example.com"]
    assert AuditLog.objects.filter(action="invite.issued").exists()

    raw_token = _token_from_email(mail.outbox[0].body)

    # --- 2. Practitioner accepts, lands on an empty draft ------------------
    user = invites.accept(raw_token)

    assert user.role == User.Role.PRACTITIONER
    assert not user.has_usable_password()

    practitioner = Practitioner.objects.get(user=user)
    assert practitioner.status == PublicationStatus.DRAFT
    assert practitioner.full_name == ""

    invite.refresh_from_db()
    assert invite.accepted_at is not None

    # --- 3. They fill it in and submit; the lint runs ----------------------
    practitioner.full_name = "Dr Amina Yusuf"
    practitioner.intro = "I support adults with anxiety, low mood and burnout."
    practitioner.services = "Assessment, formulation and weekly sessions."
    practitioner.public_email = "amina@example.com"
    practitioner.save()

    request = review.submit(practitioner, actor=user)
    practitioner.refresh_from_db()

    assert practitioner.status == PublicationStatus.SUBMITTED
    assert request.lint_flags["blocked"] is False
    assert request.snapshot["full_name"] == "Dr Amina Yusuf"

    # --- 4. Verifier works the evidence ------------------------------------
    _verify_everything(practitioner, verifier)
    assert practitioner.is_verified is True
    assert practitioner.credentials_checked_at is not None

    # --- 5. Reviewer approves; it publishes --------------------------------
    review.claim(request, actor=admin_user)
    review.approve(request, actor=admin_user)

    practitioner.refresh_from_db()
    assert practitioner.status == PublicationStatus.PUBLISHED
    assert practitioner.published_at is not None
    assert publication.is_publicly_visible(practitioner)

    # --- The audit trail names who did what --------------------------------
    actions = set(AuditLog.objects.values_list("action", flat=True))
    assert {"invite.issued", "invite.accepted", "practitioner.submitted", "practitioner.published"} <= actions

    published = AuditLog.objects.get(action="practitioner.published")
    assert published.actor == admin_user


def _token_from_email(body: str) -> str:
    line = [line for line in body.splitlines() if "/invites/accept/" in line][0]
    return line.strip().rstrip("/").rsplit("/", 1)[1]


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


def test_the_invite_token_is_only_stored_hashed(admin_user):
    invite, raw = invites.issue("x@example.com", invited_by=admin_user)

    assert invite.token_hash != raw
    assert invite.token_hash == invites.hash_token(raw)
    row = Invite.objects.filter(pk=invite.pk).values().get()
    assert raw not in str(row)


def test_an_invite_works_once(admin_user):
    _, raw = invites.issue("once@example.com", invited_by=admin_user)

    assert invites.accept(raw) is not None
    assert invites.accept(raw) is None


def test_an_expired_invite_is_refused(admin_user):
    invite, raw = invites.issue("late@example.com", invited_by=admin_user)
    Invite.objects.filter(pk=invite.pk).update(expires_at=timezone.now() - timezone.timedelta(days=1))

    assert invites.lookup(raw) is None
    assert invites.accept(raw) is None


def test_re_inviting_supersedes_the_earlier_token(admin_user):
    _, first = invites.issue("dup@example.com", invited_by=admin_user)
    _, second = invites.issue("dup@example.com", invited_by=admin_user)

    assert invites.lookup(first) is None
    assert invites.lookup(second) is not None


def test_cannot_invite_an_existing_account(admin_user, practitioner):
    with pytest.raises(invites.InviteError):
        invites.issue(practitioner.email, invited_by=admin_user)


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def test_a_blocked_submission_leaves_the_profile_in_draft(practitioner_profile):
    practitioner_profile.intro = "Titration of methylphenidate."
    practitioner_profile.save()

    with pytest.raises(review.SubmissionBlocked) as exc:
        review.submit(practitioner_profile)

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status == PublicationStatus.DRAFT
    assert "pom" in {f.rule for f in exc.value.result.blocks}


def test_a_held_submission_still_reaches_the_queue(practitioner_profile):
    practitioner_profile.intro = "A guaranteed approach."
    practitioner_profile.save()

    request = review.submit(practitioner_profile)
    practitioner_profile.refresh_from_db()

    assert practitioner_profile.status == PublicationStatus.SUBMITTED
    assert request.lint_flags["held"] is True


def test_the_snapshot_does_not_follow_later_edits(practitioner_profile):
    """A reviewer approves what was submitted, not what it became."""
    request = review.submit(practitioner_profile)

    practitioner_profile.intro = "Completely different copy."
    practitioner_profile.save()

    request.refresh_from_db()
    assert request.snapshot["intro"] != "Completely different copy."


# ---------------------------------------------------------------------------
# Review decisions
# ---------------------------------------------------------------------------


def test_request_changes_needs_notes(practitioner_profile, admin_user):
    request = review.submit(practitioner_profile)

    with pytest.raises(review.NotReviewable):
        review.request_changes(request, actor=admin_user, notes="  ")


def test_request_changes_sends_it_back(practitioner_profile, admin_user):
    request = review.submit(practitioner_profile)

    review.request_changes(request, actor=admin_user, notes="Please shorten the intro.")

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status == PublicationStatus.CHANGES_REQUESTED
    request.refresh_from_db()
    assert request.reviewer_notes == "Please shorten the intro."


def test_rejecting_removes_the_listing(practitioner_profile, admin_user):
    request = review.submit(practitioner_profile)

    review.reject(request, actor=admin_user, notes="Not eligible.")

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status == PublicationStatus.REMOVED


def test_a_practitioner_cannot_review_their_own_submission(practitioner_profile):
    from django.core.exceptions import PermissionDenied

    request = review.submit(practitioner_profile)

    with pytest.raises(PermissionDenied):
        review.approve(request, actor=practitioner_profile.user)


def test_approving_an_edit_does_not_reset_published_at(practitioner_profile, admin_user):
    first = review.submit(practitioner_profile)
    review.approve(first, actor=admin_user)
    practitioner_profile.refresh_from_db()
    original = practitioner_profile.published_at

    practitioner_profile.status = PublicationStatus.SUBMITTED
    practitioner_profile.save()
    second = review.submit(practitioner_profile)
    review.approve(second, actor=admin_user)

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.published_at == original
    assert AuditLog.objects.filter(action="practitioner.republished").exists()


# ---------------------------------------------------------------------------
# Restricted titles — §3, enforced at review
# ---------------------------------------------------------------------------


def test_a_restricted_title_cannot_be_published_without_a_verified_registration(
    practitioner_profile, admin_user
):
    from directory.factories import ProfessionFactory

    practitioner_profile.profession = ProfessionFactory(restricted_title=True)
    practitioner_profile.save()
    request = review.submit(practitioner_profile)

    with pytest.raises(review.NotReviewable, match="restricted title"):
        review.approve(request, actor=admin_user)

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status != PublicationStatus.PUBLISHED


def test_it_publishes_once_the_registration_is_verified(practitioner_profile, admin_user):
    from directory.factories import ProfessionFactory

    practitioner_profile.profession = ProfessionFactory(restricted_title=True)
    practitioner_profile.save()
    Registration.objects.create(
        practitioner=practitioner_profile, body="GMC", registration_no="1234567", verified=True
    )
    request = review.submit(practitioner_profile)

    review.approve(request, actor=admin_user)

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status == PublicationStatus.PUBLISHED


# ---------------------------------------------------------------------------
# Suspension
# ---------------------------------------------------------------------------


def test_suspension_is_immediate_and_needs_a_reason(practitioner_profile, admin_user):
    request = review.submit(practitioner_profile)
    review.approve(request, actor=admin_user)
    practitioner_profile.refresh_from_db()

    with pytest.raises(publication.SuspensionError):
        publication.suspend(practitioner_profile, actor=admin_user, reason="")

    publication.suspend(practitioner_profile, actor=admin_user, reason="Registration query")

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status == PublicationStatus.SUSPENDED
    assert not publication.is_publicly_visible(practitioner_profile)
    assert practitioner_profile.suspended_by == admin_user


def test_the_suspension_reason_is_recorded_but_never_public(practitioner_profile, admin_user):
    """The reason is staff-and-audit only — see the publication module docstring."""
    request = review.submit(practitioner_profile)
    review.approve(request, actor=admin_user)
    practitioner_profile.refresh_from_db()

    publication.suspend(practitioner_profile, actor=admin_user, reason="Under investigation")

    entry = AuditLog.objects.get(action="practitioner.suspended")
    assert entry.after["reason"] == "Under investigation"
    # is_publicly_visible is the only thing the public profile asks, and it is a
    # bare boolean — there is no route from it to the reason.
    assert publication.is_publicly_visible(practitioner_profile) is False


def test_lifting_a_suspension_republishes(practitioner_profile, admin_user):
    request = review.submit(practitioner_profile)
    review.approve(request, actor=admin_user)
    practitioner_profile.refresh_from_db()
    publication.suspend(practitioner_profile, actor=admin_user, reason="Query")

    publication.lift_suspension(practitioner_profile, actor=admin_user)

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.status == PublicationStatus.PUBLISHED
    assert practitioner_profile.suspend_reason == ""


def test_unpublishing_records_whether_the_practitioner_withdrew(practitioner_profile, admin_user):
    publication.unpublish(
        practitioner_profile,
        actor=practitioner_profile.user,
        withdrawn_by_practitioner=True,
    )

    entry = AuditLog.objects.get(action="practitioner.unpublished")
    assert entry.after["withdrawn_by_practitioner"] is True


# ---------------------------------------------------------------------------
# Provisional DBS
# ---------------------------------------------------------------------------


def test_a_second_provisional_extension_is_blocked(verifier):
    from directory.factories import PractitionerFactory

    practitioner = PractitionerFactory(provisional_dbs=True)
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL
    assert practitioner.provisional_extensions == 0

    # First extension: allowed.
    verification.extend_provisional_dbs(practitioner, actor=verifier)
    practitioner.refresh_from_db()
    assert practitioner.provisional_extensions == 1

    # Second: refused, pending Dr. Abbass sign-off.
    with pytest.raises(verification.ExtensionRequiresSignOff, match="Dr. Abbass"):
        verification.extend_provisional_dbs(practitioner, actor=verifier)

    practitioner.refresh_from_db()
    assert practitioner.provisional_extensions == 1


def test_an_extension_is_audit_logged_with_its_number(verifier):
    from directory.factories import PractitionerFactory

    practitioner = PractitionerFactory(provisional_dbs=True)
    verification.extend_provisional_dbs(practitioner, actor=verifier, note="DBS chased")

    entry = AuditLog.objects.get(action="verification.dbs.provisional_extended")
    assert entry.actor == verifier
    assert entry.after["extension_number"] == 1


def test_extending_without_a_window_raises(verifier):
    from directory.factories import PractitionerFactory

    practitioner = PractitionerFactory()

    with pytest.raises(ValueError, match="no provisional DBS window"):
        verification.extend_provisional_dbs(practitioner, actor=verifier)


# ---------------------------------------------------------------------------
# Workbench expiry rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "check_type", [VerificationType.INSURANCE, VerificationType.DBS, VerificationType.ICO]
)
def test_verifying_an_expiring_type_requires_an_expiry(verifier, check_type):
    from directory.factories import PractitionerFactory

    practitioner = PractitionerFactory()
    check, _ = practitioner.verifications.get_or_create(type=check_type)

    with pytest.raises(verification.ExpiryRequired):
        verification.set_check_status(
            check, status=VerificationStatus.VERIFIED, actor=verifier, expires_at=None
        )


def test_a_non_expiring_type_needs_no_expiry(verifier):
    from directory.factories import PractitionerFactory

    practitioner = PractitionerFactory()
    check, _ = practitioner.verifications.get_or_create(type=VerificationType.IDENTITY)

    verification.set_check_status(check, status=VerificationStatus.VERIFIED, actor=verifier)

    check.refresh_from_db()
    assert check.status == VerificationStatus.VERIFIED


def test_setting_a_check_recomputes_rather_than_writing_the_badge(verifier):
    from directory.factories import PractitionerFactory

    practitioner = PractitionerFactory()
    assert practitioner.is_verified is False

    for check_type in verification.required_types(practitioner):
        check, _ = practitioner.verifications.get_or_create(type=check_type)
        verification.set_check_status(
            check,
            status=VerificationStatus.VERIFIED,
            actor=verifier,
            expires_at=timezone.now() + timezone.timedelta(days=365)
            if check_type in verification.EXPIRING_TYPES
            else None,
        )

    practitioner.refresh_from_db()
    assert practitioner.is_verified is True

    # And it derives back down when the evidence goes.
    practitioner.verifications.all().delete()
    verification.recompute(practitioner)
    assert practitioner.is_verified is False
