"""The back-office pages render and their actions work through HTTP.

The service tests cover the rules; these cover the wiring — that a reviewer can
actually reach the queue, see the lint flags, and record a decision, and that the
pages a second extension should be blocked on say so.
"""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from backoffice.services import concerns as concerns_service
from backoffice.services import review
from directory.factories import PractitionerFactory
from directory.models import ConcernReport, PublicationStatus
from directory.services import verification

pytestmark = pytest.mark.django_db


def _staff(client, user):
    device = TOTPDevice.objects.create(user=user, confirmed=True, name="default")
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


@pytest.fixture
def submitted(practitioner):
    profile = PractitionerFactory(
        user=practitioner,
        full_name="Dr Queue Test",
        intro="I support adults with anxiety.",
        services="Weekly sessions.",
        public_email="queue@example.com",
    )
    return review.submit(profile)


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


def test_the_queue_lists_a_submission(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:review_queue")).content.decode()

    assert "Dr Queue Test" in body


def test_opening_a_submission_claims_it(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    staff.get(reverse("backoffice:review_detail", kwargs={"pk": submitted.pk}))

    submitted.practitioner.refresh_from_db()
    assert submitted.practitioner.status == PublicationStatus.IN_REVIEW


def test_lint_flags_are_surfaced_on_the_review_page(client, admin_user, practitioner):
    profile = PractitionerFactory(
        user=practitioner,
        full_name="Dr Flagged",
        intro="A guaranteed cure for anxiety.",
        public_email="flag@example.com",
    )
    request = review.submit(profile)
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:review_detail", kwargs={"pk": request.pk})).content.decode()

    assert "Lint findings" in body
    assert "efficacy" in body
    assert "holds for review" in body


def test_the_reviewer_sees_the_snapshot(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:review_detail", kwargs={"pk": submitted.pk})).content.decode()

    assert "Submitted content" in body
    assert "Frozen at" in body


def test_approving_through_the_form_publishes(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    response = staff.post(
        reverse("backoffice:review_detail", kwargs={"pk": submitted.pk}),
        {"decision": "approve", "notes": ""},
    )

    assert response.status_code == 302
    submitted.practitioner.refresh_from_db()
    assert submitted.practitioner.status == PublicationStatus.PUBLISHED


def test_requesting_changes_without_notes_is_rejected_by_the_form(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    response = staff.post(
        reverse("backoffice:review_detail", kwargs={"pk": submitted.pk}),
        {"decision": "request_changes", "notes": ""},
    )

    assert response.status_code == 200
    submitted.practitioner.refresh_from_db()
    assert submitted.practitioner.status != PublicationStatus.CHANGES_REQUESTED


# ---------------------------------------------------------------------------
# Verification workbench
# ---------------------------------------------------------------------------


def test_the_workbench_shows_a_row_for_every_type_including_missing_ones(client, verifier):
    profile = PractitionerFactory(full_name="Dr Workbench")
    staff = _staff(client, verifier)

    body = staff.get(reverse("backoffice:verification_workbench", kwargs={"pk": profile.pk})).content.decode()

    assert "Photo ID" in body
    assert "Enhanced DBS" in body
    assert "No evidence uploaded" in body


def test_the_workbench_says_the_fields_are_computed(client, verifier):
    profile = PractitionerFactory()
    staff = _staff(client, verifier)

    body = staff.get(reverse("backoffice:verification_workbench", kwargs={"pk": profile.pk})).content.decode()

    assert "computed from the rows below" in body


def test_an_admin_sees_no_open_evidence_button(client, admin_user):
    from django.core.files.base import ContentFile

    from directory.models import Document, VerificationType

    profile = PractitionerFactory()
    doc = Document(
        practitioner=profile,
        type=VerificationType.DBS,
        original_filename="dbs.pdf",
        mime_type="application/pdf",
        size_bytes=10,
        sha256="0" * 64,
    )
    doc.file.save("dbs.pdf", ContentFile(b"x"), save=True)

    staff = _staff(client, admin_user)
    body = staff.get(reverse("backoffice:verification_workbench", kwargs={"pk": profile.pk})).content.decode()

    assert "Opening evidence needs the verifier role" in body
    assert "Open evidence" not in body


# ---------------------------------------------------------------------------
# Provisional DBS
# ---------------------------------------------------------------------------


def test_the_grant_page_states_the_scope_limit_verbatim(client, verifier):
    profile = PractitionerFactory()
    staff = _staff(client, verifier)

    body = staff.get(reverse("backoffice:provisional_grant", kwargs={"pk": profile.pk})).content.decode()

    assert "publishes the listing for adult work only" in body
    assert "Under-18 client groups" in body
    assert "stay hidden until the DBS is verified" in body


def test_granting_requires_the_confirmation_checkbox(client, verifier):
    profile = PractitionerFactory()
    staff = _staff(client, verifier)

    response = staff.post(reverse("backoffice:provisional_grant", kwargs={"pk": profile.pk}), {"note": ""})

    assert response.status_code == 200
    profile.refresh_from_db()
    assert profile.provisional_expires_at is None


def test_an_admin_cannot_reach_the_grant_page(client, admin_user):
    profile = PractitionerFactory()
    staff = _staff(client, admin_user)

    response = staff.get(reverse("backoffice:provisional_grant", kwargs={"pk": profile.pk}))

    assert response.status_code == 403


def test_the_second_extension_is_blocked_in_the_ui(client, verifier):
    profile = PractitionerFactory(provisional_dbs=True)
    verification.extend_provisional_dbs(profile, actor=verifier)
    profile.refresh_from_db()

    staff = _staff(client, verifier)
    body = staff.get(reverse("backoffice:provisional_extend", kwargs={"pk": profile.pk})).content.decode()

    assert "needs Dr. Abbass to sign off" in body
    assert "Extend window" not in body


def test_posting_a_second_extension_changes_nothing(client, verifier):
    profile = PractitionerFactory(provisional_dbs=True)
    verification.extend_provisional_dbs(profile, actor=verifier)
    profile.refresh_from_db()
    before = profile.provisional_extensions

    staff = _staff(client, verifier)
    staff.post(
        reverse("backoffice:provisional_extend", kwargs={"pk": profile.pk}),
        {"confirm": "on", "note": "again"},
    )

    profile.refresh_from_db()
    assert profile.provisional_extensions == before


# ---------------------------------------------------------------------------
# Suspend
# ---------------------------------------------------------------------------


def test_the_suspend_page_says_the_public_reason_stays_private(client, admin_user):
    profile = PractitionerFactory(published=True)
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:suspend", kwargs={"pk": profile.pk})).content.decode()

    assert "takes effect immediately" in body
    assert "never say why" in body


def test_suspending_through_the_form(client, admin_user):
    profile = PractitionerFactory(published=True)
    staff = _staff(client, admin_user)

    response = staff.post(
        reverse("backoffice:suspend", kwargs={"pk": profile.pk}),
        {"reason": "Registration under query"},
    )

    assert response.status_code == 302
    profile.refresh_from_db()
    assert profile.status == PublicationStatus.SUSPENDED


# ---------------------------------------------------------------------------
# Concerns and audit
# ---------------------------------------------------------------------------


def test_the_concern_queue_puts_registration_doubts_first(client, admin_user):
    profile = PractitionerFactory(published=True, full_name="Dr Concerned")
    ConcernReport.objects.create(
        practitioner=profile, category=ConcernReport.Category.INACCURATE, detail="Wrong phone."
    )
    ConcernReport.objects.create(
        practitioner=profile,
        category=ConcernReport.Category.NOT_REGISTERED,
        detail="Not on the GMC register.",
    )

    ordered = list(concerns_service.open_queue())
    assert ordered[0].category == ConcernReport.Category.NOT_REGISTERED

    staff = _staff(client, admin_user)
    assert staff.get(reverse("backoffice:concern_queue")).status_code == 200


def test_resolving_a_concern_needs_an_outcome(client, admin_user):
    profile = PractitionerFactory(published=True)
    concern = ConcernReport.objects.create(
        practitioner=profile, category=ConcernReport.Category.OTHER, detail="Something."
    )
    staff = _staff(client, admin_user)

    response = staff.post(reverse("backoffice:concern_detail", kwargs={"pk": concern.pk}), {"outcome": ""})

    assert response.status_code == 200
    concern.refresh_from_db()
    assert concern.handled_at is None


def test_resolving_a_concern_records_the_outcome(client, admin_user):
    profile = PractitionerFactory(published=True)
    concern = ConcernReport.objects.create(
        practitioner=profile, category=ConcernReport.Category.INACCURATE, detail="Wrong phone."
    )
    staff = _staff(client, admin_user)

    staff.post(
        reverse("backoffice:concern_detail", kwargs={"pk": concern.pk}),
        {"outcome": "Corrected the number with the practitioner."},
    )

    concern.refresh_from_db()
    assert concern.handled_at is not None
    assert "Corrected" in concern.outcome


def test_the_audit_viewer_filters_and_is_read_only(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:audit_log"), {"action": "submitted"}).content.decode()

    assert "practitioner.submitted" in body
    assert "Read-only" in body
    # No destructive control anywhere on the page.
    for word in ("Delete", "Remove", "Edit"):
        assert word not in body


def test_the_audit_viewer_filters_by_actor(client, admin_user, submitted):
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:audit_log"), {"actor": "nobody@example.com"}).content.decode()

    assert "No entries match" in body


def test_every_backoffice_page_is_noindex(client, admin_user):
    staff = _staff(client, admin_user)

    body = staff.get(reverse("backoffice:dashboard")).content.decode()

    assert 'content="noindex, nofollow"' in body


def test_robots_disallows_the_back_office(client):
    body = client.get("/robots.txt").content.decode()
    assert "Disallow: /backoffice/" in body
