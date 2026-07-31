"""Granting a badge in one action, and withdrawing it when the claim changes.

Both halves of the workflow an admin actually wants: say "I have seen this
person's documents" once instead of five times, and have the badge come off by
itself when the practitioner changes the details it was granted against.

Neither is a toggle, and the tests are written to hold that line. The one that
matters most is `test_approving_the_wording_does_not_restore_the_badge`: an admin
who approves a name change has approved *words*, and must not thereby re-assert
that somebody's photo ID matches the new name.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.backoffice.services import review
from apps.directory.factories import ClientGroupFactory, PractitionerFactory, VerificationCheckFactory
from apps.directory.models import (
    AuditLog,
    MinorWorkStatus,
    Practitioner,
    PublicationStatus,
    Registration,
    ReviewRequest,
    VerificationCheck,
    VerificationStatus,
    VerificationType,
)
from apps.directory.services import verification

pytestmark = pytest.mark.django_db

NEXT_YEAR = timezone.now() + timedelta(days=365)


def _staff(client, user):
    """Sign in past the two-factor middleware.

    The `verified_2fa` fixture decorates an in-memory user, which is enough for
    the access predicates but does not survive an HTTP round trip — the
    middleware reads the session. Same helper shape as test_views.py.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice

    device = TOTPDevice.objects.create(user=user, confirmed=True, name="default")
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


# ---------------------------------------------------------------------------
# One-click verify
# ---------------------------------------------------------------------------


def test_one_action_grants_the_badge(verifier):
    practitioner = PractitionerFactory(published=True, slug="one-click")
    assert not practitioner.is_verified

    verification.verify_all_required(
        practitioner,
        actor=verifier,
        expires_at={VerificationType.INSURANCE: NEXT_YEAR},
    )
    practitioner.refresh_from_db()

    assert practitioner.is_verified
    assert practitioner.credentials_checked_at is not None


def test_it_records_every_required_check_as_evidence(verifier):
    """Not a boolean — the same rows a verifier would fill in one at a time."""
    practitioner = PractitionerFactory(published=True, slug="evidence-rows")

    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )

    verified = set(
        practitioner.verifications.filter(status=VerificationStatus.VERIFIED).values_list("type", flat=True)
    )
    assert verified == set(verification.required_types(practitioner))
    for check in practitioner.verifications.all():
        assert check.checked_by == verifier
        assert check.checked_at is not None


def test_a_prescriber_also_needs_their_prescriber_check(verifier):
    practitioner = PractitionerFactory(published=True, slug="prescriber", prescriber=True)

    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    practitioner.refresh_from_db()

    assert practitioner.is_verified
    assert practitioner.verifications.filter(
        type=VerificationType.PRESCRIBER, status=VerificationStatus.VERIFIED
    ).exists()


def test_the_insurance_expiry_is_required(verifier):
    """The date is the whole reason a one-click grant is safe to offer."""
    practitioner = PractitionerFactory(published=True, slug="no-date")

    with pytest.raises(verification.ExpiryRequired):
        verification.verify_all_required(practitioner, actor=verifier)

    practitioner.refresh_from_db()
    assert not practitioner.is_verified


def test_the_badge_still_lapses_on_its_own(verifier):
    """The point of keeping it computed. Nobody has to remember anything."""
    with freeze_time("2026-01-01 09:00:00"):
        practitioner = PractitionerFactory(published=True, slug="lapses")
        verification.verify_all_required(
            practitioner,
            actor=verifier,
            expires_at={VerificationType.INSURANCE: timezone.now() + timedelta(days=30)},
        )
        practitioner.refresh_from_db()
        assert practitioner.is_verified

    with freeze_time("2026-03-01 03:00:00"):
        verification.nightly_sweep()

    practitioner.refresh_from_db()
    assert not practitioner.is_verified


def test_it_does_not_re_date_a_check_nobody_looked_at_again(verifier):
    """The badge shows the OLDEST required check.

    Silently re-dating one that was already verified would make the badge claim
    to be fresher than the evidence behind it.
    """
    practitioner = PractitionerFactory(published=True, slug="oldest")
    old = timezone.now() - timedelta(days=200)
    VerificationCheckFactory(
        practitioner=practitioner,
        type=VerificationType.IDENTITY,
        status=VerificationStatus.VERIFIED,
        checked_at=old,
    )

    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    practitioner.refresh_from_db()

    identity = practitioner.verifications.get(type=VerificationType.IDENTITY)
    assert identity.checked_at == old
    assert practitioner.credentials_checked_at == old


def test_it_does_not_grant_a_dbs(verifier):
    """DBS is the safeguarding check, and it stays a deliberate separate act.

    It is not one of the badge's required checks, so a bulk grant must not sweep
    it up — a practitioner working with under-18s gets a badge and still has
    their minor groups hidden.
    """
    practitioner = PractitionerFactory(published=True, slug="no-dbs-sweep")
    practitioner.client_groups.add(ClientGroupFactory(minors=True))

    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    practitioner.refresh_from_db()

    assert practitioner.is_verified
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED
    assert not practitioner.verifications.filter(
        type=VerificationType.DBS, status=VerificationStatus.VERIFIED
    ).exists()


def test_a_second_grant_with_nothing_outstanding_says_so(verifier):
    practitioner = PractitionerFactory(published=True, slug="already-done")
    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )

    with pytest.raises(verification.NothingToVerify):
        verification.verify_all_required(
            practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
        )


def test_the_grant_is_audit_logged_against_a_named_person(verifier):
    practitioner = PractitionerFactory(published=True, slug="audited")

    verification.verify_all_required(
        practitioner,
        actor=verifier,
        expires_at={VerificationType.INSURANCE: NEXT_YEAR},
        notes="Saw all five in the folder.",
    )

    entry = AuditLog.objects.get(action="verification.verified_all_required")
    assert entry.actor == verifier
    assert entry.entity_id == str(practitioner.pk)
    assert entry.after["notes"] == "Saw all five in the folder."
    # And one row per check as well, from set_check_status().
    assert AuditLog.objects.filter(action__startswith="verification.identity.").exists()


# ---------------------------------------------------------------------------
# The view, and who may use it
# ---------------------------------------------------------------------------


def _verify_all_url(practitioner):
    return reverse("backoffice:verification_verify_all", args=[practitioner.pk])


def test_the_workbench_offers_the_action_to_a_verifier(client, verifier):
    practitioner = PractitionerFactory(published=True, slug="workbench-verifier")
    _staff(client, verifier)

    body = client.get(reverse("backoffice:verification_workbench", args=[practitioner.pk])).content.decode()

    assert "Verify all required checks" in body


def test_the_action_grants_the_badge_through_the_view(client, verifier):
    practitioner = PractitionerFactory(published=True, slug="through-the-view")
    _staff(client, verifier)

    response = client.post(
        _verify_all_url(practitioner),
        {
            "insurance_expires_at": NEXT_YEAR.strftime("%Y-%m-%dT%H:%M"),
            "notes": "",
            "confirm": "on",
        },
    )
    practitioner.refresh_from_db()

    assert response.status_code == 302
    assert practitioner.is_verified


def test_an_admin_cannot_grant_a_badge(client, admin_user):
    """Gated on can_view_evidence, not can_review_submissions.

    Granting a badge is asserting that documents were checked. The role that
    cannot open a passport scan cannot assert it checked one.
    """
    practitioner = PractitionerFactory(published=True, slug="admin-blocked")
    _staff(client, admin_user)

    response = client.post(
        _verify_all_url(practitioner),
        {"insurance_expires_at": NEXT_YEAR.strftime("%Y-%m-%dT%H:%M"), "confirm": "on"},
    )
    practitioner.refresh_from_db()

    assert response.status_code == 403
    assert not practitioner.is_verified


def test_a_missing_date_is_refused_by_the_view(client, verifier):
    practitioner = PractitionerFactory(published=True, slug="view-no-date")
    _staff(client, verifier)

    client.post(_verify_all_url(practitioner), {"confirm": "on"})
    practitioner.refresh_from_db()

    assert not practitioner.is_verified


def test_the_confirmation_box_is_required(client, verifier):
    practitioner = PractitionerFactory(published=True, slug="unconfirmed")
    _staff(client, verifier)

    client.post(
        _verify_all_url(practitioner),
        {"insurance_expires_at": NEXT_YEAR.strftime("%Y-%m-%dT%H:%M")},
    )
    practitioner.refresh_from_db()

    assert not practitioner.is_verified


# ---------------------------------------------------------------------------
# Re-review when a published listing is edited
# ---------------------------------------------------------------------------


@pytest.fixture
def live_and_verified(verifier):
    practitioner = PractitionerFactory(published=True, slug="live-example", full_name="Jane Example")
    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    practitioner.refresh_from_db()
    assert practitioner.is_verified
    return practitioner


def test_a_controlled_edit_withdraws_the_badge(live_and_verified):
    live_and_verified.full_name = "Jane Married-Name"
    live_and_verified.save()

    request = review.submit_update(
        live_and_verified, changed_fields=["full_name"], actor=live_and_verified.user
    )
    live_and_verified.refresh_from_db()

    assert request is not None
    assert not live_and_verified.is_verified


def test_the_listing_stays_live_while_the_edit_is_reviewed(client, live_and_verified):
    """Pulling a page down because somebody fixed their own surname would punish
    keeping a listing accurate. What is at risk is the badge, not the listing."""
    review.submit_update(live_and_verified, changed_fields=["full_name"], actor=None)
    live_and_verified.refresh_from_db()

    assert live_and_verified.status == PublicationStatus.PUBLISHED

    response = client.get(f"/p/{live_and_verified.slug}/")
    body = response.content.decode()
    assert response.status_code == 200
    assert "Credentials checked" not in body


def test_a_safe_edit_changes_nothing(live_and_verified):
    """Bio, availability, photo, fees publish immediately and keep the badge."""
    assert review.submit_update(live_and_verified, changed_fields=["intro", "fee_min"]) is None

    live_and_verified.refresh_from_db()
    assert live_and_verified.is_verified
    assert not ReviewRequest.objects.exists()


@pytest.mark.parametrize(
    ("field", "reopened"),
    [
        ("full_name", {VerificationType.IDENTITY, VerificationType.REGISTRATION}),
        ("profession", {VerificationType.REGISTRATION}),
        ("registrations", {VerificationType.REGISTRATION}),
        ("qualifications", {VerificationType.QUALIFICATION}),
    ],
)
def test_an_edit_reopens_the_checks_that_were_made_against_it(live_and_verified, field, reopened):
    """The mapping is "what was this check made against?".

    A verifier who confirmed a GMC number confirmed *that number*. Change it and
    the confirmation is about a number no longer on the listing.
    """
    review.submit_update(live_and_verified, changed_fields=[field])

    outstanding = set(
        live_and_verified.verifications.exclude(status=VerificationStatus.VERIFIED).values_list(
            "type", flat=True
        )
    )
    assert outstanding == reopened


def test_the_edit_reaches_the_review_queue(live_and_verified):
    """A withdrawn badge that sits in no queue is a practitioner waiting for a
    re-check nobody can see they are owed."""
    review.submit_update(live_and_verified, changed_fields=["full_name"])

    queued = list(review.open_queue())
    assert len(queued) == 1
    assert queued[0].practitioner_id == live_and_verified.pk
    assert queued[0].changed_fields == ["full_name"]


def test_approving_the_wording_does_not_restore_the_badge(live_and_verified, admin_user):
    """THE line this whole design holds.

    Approving copy is not re-checking documents. An admin who accepts a name
    change must not thereby assert that somebody's photo ID matches the new name.
    """
    request = review.submit_update(live_and_verified, changed_fields=["full_name"])

    review.approve_update(request, actor=admin_user, notes="Name change looks fine.")
    live_and_verified.refresh_from_db()
    request.refresh_from_db()

    assert request.outcome == "approved"
    assert live_and_verified.status == PublicationStatus.PUBLISHED
    assert not live_and_verified.is_verified


def test_re_verifying_the_evidence_restores_the_badge(live_and_verified, verifier):
    """And this is the only thing that does."""
    review.submit_update(live_and_verified, changed_fields=["full_name"])
    live_and_verified.refresh_from_db()
    assert not live_and_verified.is_verified

    for check in live_and_verified.verifications.exclude(status=VerificationStatus.VERIFIED):
        verification.set_check_status(
            check, status=VerificationStatus.VERIFIED, actor=verifier, expires_at=check.expires_at
        )

    live_and_verified.refresh_from_db()
    assert live_and_verified.is_verified


def test_approve_update_refuses_a_listing_that_is_not_live(live_and_verified, admin_user):
    request = review.submit_update(live_and_verified, changed_fields=["full_name"])
    Practitioner.objects.filter(pk=live_and_verified.pk).update(status=PublicationStatus.SUSPENDED)
    request.refresh_from_db()

    with pytest.raises(review.NotReviewable):
        review.approve_update(request, actor=admin_user)


def test_approve_update_needs_the_reviewer_role(live_and_verified, practitioner):
    request = review.submit_update(live_and_verified, changed_fields=["full_name"])

    with pytest.raises(PermissionDenied):
        review.approve_update(request, actor=practitioner)


def test_an_unpublished_listing_uses_the_ordinary_submit_path(verifier):
    """No badge to protect and no page to keep up."""
    draft = PractitionerFactory(slug="draft-example")

    assert review.submit_update(draft, changed_fields=["full_name"]) is None


def test_a_client_group_change_moves_the_minor_gate_not_the_badge(live_and_verified):
    """recompute() already derives the under-18 requirement from client groups,
    so nothing in the invalidation map needs to."""
    live_and_verified.client_groups.add(ClientGroupFactory(minors=True))
    review.submit_update(live_and_verified, changed_fields=["client_groups"])
    live_and_verified.refresh_from_db()

    assert live_and_verified.minor_work_status == MinorWorkStatus.BLOCKED
    # The badge's own required checks were untouched by a client-group change.
    assert live_and_verified.is_verified


def test_the_reopening_is_audit_logged(live_and_verified, admin_user):
    review.submit_update(live_and_verified, changed_fields=["full_name"], actor=admin_user)

    entry = AuditLog.objects.get(action="verification.reopened_after_edit")
    assert entry.actor == admin_user
    assert entry.after["changed_fields"] == ["full_name"]
    assert AuditLog.objects.filter(action="practitioner.update_submitted").exists()


def test_reopening_leaves_an_already_outstanding_check_alone(live_and_verified):
    """Resetting one that was never verified would wipe its history for nothing."""
    VerificationCheck.objects.filter(
        practitioner=live_and_verified, type=VerificationType.QUALIFICATION
    ).update(status=VerificationStatus.IN_REVIEW, checked_at=None)

    review.submit_update(live_and_verified, changed_fields=["post_nominals"])

    qualification = live_and_verified.verifications.get(type=VerificationType.QUALIFICATION)
    assert qualification.status == VerificationStatus.IN_REVIEW


# ---------------------------------------------------------------------------
# Through the admin, which is where edits actually happen today
# ---------------------------------------------------------------------------


def _serialise(form, prefix=""):
    """One form's initial values, as a browser would post them."""
    data = {}
    for name, field in form.fields.items():
        value = form.initial.get(name, field.initial)
        key = f"{prefix}{name}"
        if value is None or value == "":
            continue
        if hasattr(value, "all"):
            data[key] = [str(v.pk) for v in value.all()]
        elif isinstance(value, list | tuple):
            data[key] = [str(v) for v in value]
        elif hasattr(value, "pk"):
            data[key] = str(value.pk)
        elif value is True:
            data[key] = "on"
        elif value is False:
            continue
        else:
            data[key] = str(value)
    return data


def _admin_change_post(client, practitioner, **overrides):
    """A real admin change-form POST, built from the rendered page.

    Reconstructing the payload by hand would test a payload I invented. Reading
    the actual form and every inline row means this exercises the same request
    path a member of staff does — which is the only way to know `save_related` is
    really wired in.

    The inline ROWS matter, not just the management forms: a practitioner with
    verification checks has four or five rows in the `verifications` inline, and
    posting TOTAL_FORMS without their data fails the formset. The POST then
    re-renders with errors and saves nothing — which looks exactly like the hook
    not firing.
    """
    url = reverse("admin:directory_practitioner_change", args=[practitioner.pk])
    response = client.get(url)
    assert response.status_code == 200

    data = _serialise(response.context["adminform"].form)

    for inline in response.context["inline_admin_formsets"]:
        formset = inline.formset
        prefix = formset.prefix
        data[f"{prefix}-TOTAL_FORMS"] = str(formset.total_form_count())
        data[f"{prefix}-INITIAL_FORMS"] = str(formset.initial_form_count())
        data[f"{prefix}-MIN_NUM_FORMS"] = "0"
        data[f"{prefix}-MAX_NUM_FORMS"] = "1000"
        for index, form in enumerate(formset.forms):
            data.update(_serialise(form, prefix=f"{prefix}-{index}-"))

    data.update(overrides)
    return url, data


def test_editing_a_name_in_the_admin_withdraws_the_badge(client, superadmin, verifier):
    """The real request path, because a hook nothing calls is not a feature.

    `save_related` rather than `save_model`: registrations and qualifications are
    edited through inlines, which are not saved yet when `save_model` runs, so
    the earlier hook would miss exactly the credential edits this exists to catch.
    """
    practitioner = PractitionerFactory(published=True, slug="admin-edit", full_name="Jane Example")
    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    practitioner.refresh_from_db()
    assert practitioner.is_verified

    _staff(client, superadmin)
    url, data = _admin_change_post(client, practitioner, full_name="Jane Married-Name")

    response = client.post(url, data, follow=True)
    practitioner.refresh_from_db()

    assert response.status_code == 200
    assert practitioner.full_name == "Jane Married-Name"
    # The listing is still live...
    assert practitioner.status == PublicationStatus.PUBLISHED
    # ...and the badge is not.
    assert not practitioner.is_verified
    assert ReviewRequest.objects.filter(practitioner=practitioner, outcome="").exists()


def test_editing_a_bio_in_the_admin_keeps_the_badge(client, superadmin, verifier):
    """Safe fields publish immediately. Otherwise nobody ever fixes a typo."""
    practitioner = PractitionerFactory(published=True, slug="admin-bio", full_name="Jane Example")
    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    practitioner.refresh_from_db()

    _staff(client, superadmin)
    url, data = _admin_change_post(client, practitioner, intro="A slightly reworded introduction.")

    client.post(url, data, follow=True)
    practitioner.refresh_from_db()

    assert practitioner.intro == "A slightly reworded introduction."
    assert practitioner.is_verified
    assert not ReviewRequest.objects.exists()


def test_a_registration_change_counts_as_a_credential_change(live_and_verified):
    """Editing the number on file is exactly what the REGISTRATION check was made
    against, so it cannot survive the edit."""
    Registration.objects.create(
        practitioner=live_and_verified, body="GMC", registration_no="7654321", verified=True
    )

    review.submit_update(live_and_verified, changed_fields=["registrations"])
    live_and_verified.refresh_from_db()

    assert not live_and_verified.is_verified
    assert (
        live_and_verified.verifications.get(type=VerificationType.REGISTRATION).status
        == VerificationStatus.SUBMITTED
    )


# ---------------------------------------------------------------------------
# The whole loop, asserted on the public page
# ---------------------------------------------------------------------------


def test_the_full_loop_from_the_visitors_side(client, superadmin, verifier):
    """Grant, edit, withdraw, re-verify — checked on the page a client reads.

    Every other test here asserts on `is_verified`. This one only looks at what a
    visitor sees, because that is the thing being promised: the badge appears when
    an admin says the documents are good, disappears the moment the practitioner
    changes what the documents were checked against, and comes back when somebody
    has looked again.
    """
    practitioner = PractitionerFactory(published=True, slug="loop-example", full_name="Jane Example")
    profile_url = f"/p/{practitioner.slug}/"

    def badge_on_page() -> bool:
        return "Credentials checked" in client.get(profile_url).content.decode()

    # 1. Live, but nothing checked yet — no badge.
    assert not badge_on_page()

    # 2. One admin action grants it.
    verification.verify_all_required(
        practitioner, actor=verifier, expires_at={VerificationType.INSURANCE: NEXT_YEAR}
    )
    assert badge_on_page()

    # 3. The practitioner changes their name. The listing stays up; the badge goes.
    _staff(client, superadmin)
    url, data = _admin_change_post(client, practitioner, full_name="Jane Married-Name")
    client.post(url, data, follow=True)
    client.logout()

    assert client.get(profile_url).status_code == 200
    assert "Jane Married-Name" in client.get(profile_url).content.decode()
    assert not badge_on_page()

    # 4. A verifier looks at the new details and confirms them. The badge returns.
    practitioner.refresh_from_db()
    for check in practitioner.verifications.exclude(status=VerificationStatus.VERIFIED):
        verification.set_check_status(
            check, status=VerificationStatus.VERIFIED, actor=verifier, expires_at=check.expires_at
        )

    assert badge_on_page()
