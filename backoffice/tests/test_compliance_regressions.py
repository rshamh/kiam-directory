"""Regressions for the findings the compliance review raised at the Phase 2 gate.

Each of these was a real hole. They are grouped here rather than scattered so
that the reason each check exists stays attached to it.
"""

from __future__ import annotations

import pytest
from django.core import mail
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from backoffice.services import invites, publication, review
from directory.factories import ClientGroupFactory, PractitionerFactory, ProfessionFactory
from directory.models import (
    MinorWorkStatus,
    PublicationStatus,
    Registration,
    VerificationStatus,
    VerificationType,
)
from directory.services import documents as evidence
from directory.services import lint

pytestmark = pytest.mark.django_db


def _staff(client, user):
    device = TOTPDevice.objects.create(user=user, confirmed=True, name="default")
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


# ---------------------------------------------------------------------------
# BLOCKER 1 — an admin could set the badge through the workbench
# ---------------------------------------------------------------------------


def test_an_admin_cannot_verify_a_check(client, admin_user):
    """Marking a check VERIFIED recomputes minor_work_status.

    An `admin` cannot open evidence, so they must not be able to declare it
    verified — otherwise they could publish someone's under-18 client groups off
    the back of a DBS they are not permitted to look at.
    """
    profile = PractitionerFactory()
    profile.client_groups.add(ClientGroupFactory(minors=True))
    staff = _staff(client, admin_user)

    response = staff.post(
        reverse(
            "backoffice:verification_set_status",
            kwargs={"pk": profile.pk, "check_type": VerificationType.DBS},
        ),
        {"status": VerificationStatus.VERIFIED, "expires_at": "2030-01-01T00:00", "notes": ""},
    )

    assert response.status_code == 403
    profile.refresh_from_db()
    assert profile.minor_work_status != MinorWorkStatus.CLEARED
    assert not profile.verifications.filter(status=VerificationStatus.VERIFIED).exists()


def test_a_verifier_can_verify_a_check(client, verifier):
    profile = PractitionerFactory()
    staff = _staff(client, verifier)

    response = staff.post(
        reverse(
            "backoffice:verification_set_status",
            kwargs={"pk": profile.pk, "check_type": VerificationType.IDENTITY},
        ),
        {"status": VerificationStatus.VERIFIED, "expires_at": "", "notes": ""},
    )

    assert response.status_code == 302
    assert profile.verifications.filter(
        type=VerificationType.IDENTITY, status=VerificationStatus.VERIFIED
    ).exists()


def test_the_workbench_hides_the_status_form_from_an_admin(client, admin_user):
    profile = PractitionerFactory()
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:verification_workbench", kwargs={"pk": profile.pk})).content.decode()

    assert "Recording a verification decision needs the verifier role" in body
    assert "/verification/identity/" not in body
    assert 'name="status"' not in body


# ---------------------------------------------------------------------------
# BLOCKER 2 — lift_suspension bypassed the restricted-title gate
# ---------------------------------------------------------------------------


def test_lifting_a_suspension_re_applies_the_restricted_title_gate(admin_user):
    """The struck-off case, which is the reason suspension exists at all."""
    profile = PractitionerFactory(published=True, profession=ProfessionFactory(restricted_title=True))
    registration = Registration.objects.create(
        practitioner=profile, body="GMC", registration_no="1234567", verified=True
    )
    publication.suspend(profile, actor=admin_user, reason="Struck off — GMC notice")

    # The registration is withdrawn while they are suspended.
    registration.verified = False
    registration.save(update_fields=["verified"])

    with pytest.raises(publication.SuspensionError, match="restricted title"):
        publication.lift_suspension(profile, actor=admin_user)

    profile.refresh_from_db()
    assert profile.status == PublicationStatus.SUSPENDED


def test_lifting_a_suspension_works_when_nothing_blocks(admin_user):
    profile = PractitionerFactory(published=True)
    publication.suspend(profile, actor=admin_user, reason="Query")

    publication.lift_suspension(profile, actor=admin_user)

    profile.refresh_from_db()
    assert profile.status == PublicationStatus.PUBLISHED


def test_lifting_a_suspension_recomputes_a_lapsed_badge(admin_user, verifier):
    """A suspension can outlast an insurance certificate."""
    from datetime import timedelta

    from django.utils import timezone

    profile = PractitionerFactory(published=True, verified=True)
    assert profile.is_verified is True
    publication.suspend(profile, actor=admin_user, reason="Query")

    profile.verifications.filter(type=VerificationType.INSURANCE).update(
        expires_at=timezone.now() - timedelta(days=1)
    )

    publication.lift_suspension(profile, actor=admin_user)

    profile.refresh_from_db()
    assert profile.is_verified is False, "republished with a badge whose evidence had lapsed"


# ---------------------------------------------------------------------------
# WARNING 1 — the "internal" invite note was emailed to the practitioner
# ---------------------------------------------------------------------------


def test_the_internal_invite_note_is_never_emailed(admin_user):
    """The form calls it internal, so staff will write private things in it."""
    mail.outbox.clear()

    invites.issue_and_send(
        "invitee@example.com",
        invited_by=admin_user,
        note="Chased twice, seems disorganised — watch the paperwork",
    )

    body = mail.outbox[0].body
    assert "disorganised" not in body
    assert "Chased twice" not in body


def test_the_note_is_still_kept_for_staff(admin_user):
    invite = invites.issue_and_send("noted@example.com", invited_by=admin_user, note="Internal only")
    invite.refresh_from_db()
    assert invite.note == "Internal only"


# ---------------------------------------------------------------------------
# WARNING 2 — a forwarded evidence link was usable by a second verifier
# ---------------------------------------------------------------------------


def test_an_evidence_link_is_bound_to_the_person_it_was_minted_for(document, verifier, user_factory):
    """Otherwise the access log names the wrong person for the actual read."""
    from accounts.models import User

    other_verifier = user_factory("second@example.com", User.Role.VERIFIER)
    url = evidence.open_evidence(document, user=verifier)
    token = url.rstrip("/").rsplit("/", 1)[1]

    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.verify_stream_token(token, user=other_verifier)


def test_a_forwarded_link_fails_over_http_even_for_another_verifier(client, document, verifier, user_factory):
    from accounts.models import User

    other = user_factory("second@example.com", User.Role.VERIFIER)
    url = evidence.open_evidence(document, user=verifier)

    staff = _staff(client, other)
    response = staff.get(url)

    assert response.status_code == 403


def test_the_person_it_was_minted_for_can_still_use_it(client, document, verifier):
    url = evidence.open_evidence(document, user=verifier)

    staff = _staff(client, verifier)
    response = staff.get(url)

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# WARNING 3 — a restricted title claimed in post_nominals passed both gates
# ---------------------------------------------------------------------------


def test_a_restricted_title_in_post_nominals_is_flagged():
    """Unrestricted profession + protected title in the credential string."""
    profile = PractitionerFactory(
        public_email="hello@example.com",
        profession=ProfessionFactory(restricted=False, name="Counsellor"),
        post_nominals="Clinical Psychologist, BSc",
    )

    result = lint.run(profile)

    assert "restricted_title" in {f.rule for f in result.findings}
    assert result.must_hold_for_review


def test_a_restricted_title_in_display_title_is_flagged():
    profile = PractitionerFactory(
        public_email="hello@example.com", display_title="Psychiatrist", post_nominals=""
    )

    assert "restricted_title" in {f.rule for f in lint.run(profile).findings}


def test_ordinary_post_nominals_are_not_flagged():
    profile = PractitionerFactory(public_email="hello@example.com", post_nominals="MBACP (Accred), PGDip")

    assert "restricted_title" not in {f.rule for f in lint.run(profile).findings}


# ---------------------------------------------------------------------------
# NOTE 1 — stale suspended_by on a republished listing
# ---------------------------------------------------------------------------


def test_approving_clears_the_whole_suspension_triple(practitioner_profile, admin_user):
    request = review.submit(practitioner_profile)
    review.approve(request, actor=admin_user)
    practitioner_profile.refresh_from_db()
    publication.suspend(practitioner_profile, actor=admin_user, reason="Query")

    practitioner_profile.status = PublicationStatus.SUBMITTED
    practitioner_profile.save(update_fields=["status"])
    second = review.submit(practitioner_profile)
    review.approve(second, actor=admin_user)

    practitioner_profile.refresh_from_db()
    assert practitioner_profile.suspended_at is None
    assert practitioner_profile.suspended_by is None
    assert practitioner_profile.suspend_reason == ""


# ---------------------------------------------------------------------------
# NOTE 2 — concern reports were deletable
# ---------------------------------------------------------------------------


def test_concern_reports_cannot_be_deleted_from_admin():
    from django.contrib import admin as django_admin

    from directory.models import ConcernReport

    model_admin = django_admin.site._registry[ConcernReport]
    assert model_admin.has_delete_permission(None) is False
