"""The three things the Phase 6 gate asks to see demonstrated.

    "Demonstrate: a controlled-field edit re-entering review; a safe-field edit
     publishing immediately; one-click unpublish removing the profile from search
     and public view within seconds."

Each is asserted end to end through the HTTP layer — a POST from a signed-in
practitioner, then the *public* consequence — rather than against the service that
implements it. The services have their own tests; these three are about whether
the dashboard is wired to them, which is the thing a gate review can actually be
shown.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.directory.factories import PractitionerFactory
from apps.directory.models import ConsentRecord, PublicationStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def listed(client):
    """A published, verified practitioner signed in to their own dashboard."""
    user = User.objects.create_user(email="nadia@example.com", role=User.Role.PRACTITIONER)
    practitioner = PractitionerFactory(
        published=True,
        complete=True,
        verified=True,
        user=user,
        slug="nadia-haddad",
        full_name="Nadia Haddad",
        display_title="Dr",
    )
    practitioner.refresh_from_db()
    assert practitioner.is_verified, "the fixture needs a badge for the withdrawal to be visible"

    client.force_login(user)
    return practitioner


def public_profile(client, practitioner):
    return client.get(reverse("directory:profile", args=[practitioner.slug]))


# ---------------------------------------------------------------------------
# 1. A controlled-field edit re-enters review
# ---------------------------------------------------------------------------


def test_a_controlled_edit_re_enters_review_and_withdraws_the_badge(client, listed):
    """Changing a name asks for the evidence behind it to be checked again.

    And the listing **stays up**. That is the Phase 3b design and it is the half
    most likely to be "fixed" by somebody who reads "re-enters review" and expects
    a takedown: pulling a live page because a practitioner corrected their own
    surname punishes keeping a listing accurate. What is in doubt is Kiam's claim
    to have checked the details, so that is what goes.
    """
    response = client.post(
        reverse("dashboard:profile"),
        _profile_payload(listed, full_name="Nadia Haddad-Okonkwo"),
        follow=True,
    )
    assert response.status_code == 200

    listed.refresh_from_db()

    # The name changed.
    assert listed.full_name == "Nadia Haddad-Okonkwo"

    # The badge is gone, and nobody set a field to make that happen: the check it
    # was made against was reopened and `recompute()` noticed.
    assert listed.is_verified is False
    assert listed.credentials_checked_at is None

    # A review was raised...
    assert listed.review_requests.filter(outcome="").exists()
    assert listed.review_requests.latest("submitted_at").changed_fields == ["full_name"]

    # ...and the listing is STILL PUBLISHED and still readable by the public.
    assert listed.status == PublicationStatus.PUBLISHED
    assert public_profile(client, listed).status_code == 200


def test_the_practitioner_is_told_what_changing_it_will_cost_before_they_save(client, listed):
    """The brief: "the practitioner should know before saving which kind of change
    they're making." A marker on the field, and the consequence spelled out."""
    body = client.get(reverse("dashboard:profile")).content.decode()

    assert "We check this" in body
    assert "the “Credentials checked” badge comes off until we have re-checked" in body
    # And it says the listing does not come down, because that is the fear.
    assert "Your listing stays online" in body


def test_and_told_what_it_did_afterwards(client, listed):
    response = client.post(
        reverse("dashboard:profile"),
        _profile_payload(listed, full_name="Nadia Haddad-Okonkwo"),
        follow=True,
    )
    body = response.content.decode()

    assert "badge has come off your listing for now" in body
    assert "Your listing is still online" in body


def test_a_credential_change_is_controlled_even_though_it_is_a_formset(client, listed):
    """`registrations` and `qualifications` are separate models, so nothing about
    them appears in `form.changed_data`. A dashboard that inferred changed fields
    from the form alone would let somebody change their GMC number with the badge
    still on the listing — which is why `editing.save()` takes them explicitly."""
    from apps.directory.models import Registration

    Registration.objects.create(practitioner=listed, body="GMC", registration_no="1234567")
    listed.refresh_from_db()
    assert listed.is_verified

    response = client.post(reverse("dashboard:credentials"), _credentials_payload(listed), follow=True)
    assert response.status_code == 200

    listed.refresh_from_db()
    assert listed.is_verified is False
    assert "registrations" in listed.review_requests.latest("submitted_at").changed_fields


# ---------------------------------------------------------------------------
# 2. A safe-field edit publishes immediately
# ---------------------------------------------------------------------------


def test_a_safe_edit_publishes_immediately_and_keeps_the_badge(client, listed):
    """Closing your books has to be a ten-second job that costs nothing."""
    response = client.post(
        reverse("dashboard:availability"),
        _availability_payload(listed, accepting_new_clients=""),
        follow=True,
    )
    assert response.status_code == 200

    listed.refresh_from_db()
    assert listed.accepting_new_clients is False

    # No review, no badge change.
    assert not listed.review_requests.filter(outcome="").exists()
    assert listed.is_verified is True

    # And it is live on the public page straight away.
    body = public_profile(client, listed).content.decode()
    assert "Not currently accepting new clients" in body


def test_the_availability_page_says_so_before_they_touch_anything(client, listed):
    body = client.get(reverse("dashboard:availability")).content.decode()

    assert "Changes here go live as soon as you save." in body
    assert "We check this" not in body, "nothing on this page is controlled"


def test_a_safe_edit_to_free_text_is_still_linted(client, listed):
    """The lint was written for `review.submit()` — the one-time path from draft to
    published. Phase 6 is the first time a practitioner can edit a LIVE listing's
    free text, and a safe-field edit publishes immediately, so without a lint here
    a published practitioner could put a prescription-only medicine name into their
    intro and it would be public the moment they pressed Save.
    docs/content-compliance.md §1 says a match blocks submission; this is what makes
    that true of the second edit as well as the first."""
    from apps.directory.services.lint import pom_terms

    pom = next(iter(pom_terms()))

    response = client.post(
        reverse("dashboard:profile"),
        _profile_payload(listed, intro=f"I prescribe {pom} for adult ADHD. " * 12),
    )

    assert response.status_code == 200, "re-rendered with errors, not saved"
    listed.refresh_from_db()
    assert pom.lower() not in listed.intro.lower()
    assert pom.lower() in response.content.decode().lower()


# ---------------------------------------------------------------------------
# 3. One-click unpublish
# ---------------------------------------------------------------------------


def test_one_click_unpublish_removes_the_listing_from_public_view(client, listed):
    """Consent is the lawful basis for publishing this data, so withdrawal has to
    be at least as easy as giving it — one action, a confirmation, immediate."""
    response = client.post(
        reverse("dashboard:unpublish"),
        {"confirm": "TAKE DOWN", "reason": "Taking a break."},
        follow=True,
    )
    assert response.status_code == 200

    listed.refresh_from_db()
    assert listed.status == PublicationStatus.UNPUBLISHED

    # Gone from the public profile — and indistinguishable from a listing that
    # never existed, which is the Phase 3 rule.
    assert public_profile(client, listed).status_code == 404


def test_one_click_unpublish_removes_the_listing_from_search(client, listed):
    assert listed.full_name in client.get("/search/").content.decode()

    client.post(reverse("dashboard:unpublish"), {"confirm": "TAKE DOWN"}, follow=True)

    assert listed.full_name not in client.get("/search/").content.decode()


def test_one_click_unpublish_removes_the_listing_from_the_home_page(client, listed):
    """The grid is cached for 24 hours, so this only holds because
    `publication.bust_cache()` runs — and because the cache holds primary keys that
    are re-read with `status=PUBLISHED` on the way out."""
    assert listed.full_name in client.get("/").content.decode()

    client.post(reverse("dashboard:unpublish"), {"confirm": "TAKE DOWN"}, follow=True)

    assert listed.full_name not in client.get("/").content.decode()


def test_unpublishing_writes_the_consent_withdrawal(client, listed):
    """The GDPR half. An unpublish that forgets this leaves the record saying the
    practitioner still consents to a listing that is gone."""
    consent = ConsentRecord.objects.create(
        practitioner=listed,
        terms_version="listing-agreement-v1.0",
        consent_publish=True,
        consent_registered=True,
        consent_independent=True,
        consent_accurate=True,
        consent_removal_path=True,
        signed_name="Nadia Haddad",
    )
    assert consent.withdrawn_at is None

    client.post(reverse("dashboard:unpublish"), {"confirm": "TAKE DOWN"}, follow=True)

    consent.refresh_from_db()
    assert consent.withdrawn_at is not None


def test_the_withdrawal_is_recorded_as_the_practitioner_s_act_not_an_enforcement(client, listed):
    """Same visible outcome as a suspension, different record. A practitioner who
    withdrew consent must not read later as somebody Kiam took action against."""
    from apps.directory.models import AuditLog

    client.post(reverse("dashboard:unpublish"), {"confirm": "TAKE DOWN"}, follow=True)

    actions = set(AuditLog.objects.filter(entity_id=str(listed.pk)).values_list("action", flat=True))
    assert "consent.withdrawn" in actions
    assert "practitioner.suspended" not in actions

    unpublished = AuditLog.objects.get(entity_id=str(listed.pk), action="practitioner.unpublished")
    assert unpublished.after["withdrawn_by_practitioner"] is True


def test_there_is_no_contact_us_to_be_removed_anywhere_in_the_flow(client, listed):
    """The brief, verbatim: "No 'contact us to be removed' anywhere." That wording
    on the paper intake form is being changed to match this page."""
    for name in ("dashboard:security", "dashboard:unpublish", "dashboard:remove"):
        body = client.get(reverse(name)).content.decode().lower()
        assert "contact us to be removed" not in body
        assert "email us to be removed" not in body
        assert "get in touch to remove" not in body


def test_taking_the_listing_down_needs_a_deliberate_confirmation(client, listed):
    """Not a checkbox. This is the action somebody is most likely to trigger by
    accident from a keyboard and most likely to be upset about."""
    response = client.post(reverse("dashboard:unpublish"), {"confirm": "yes"})

    assert response.status_code == 200
    listed.refresh_from_db()
    assert listed.status == PublicationStatus.PUBLISHED


def test_full_removal_schedules_the_evidence_for_deletion(client, listed, settings):
    """The stronger ask, offered separately: "take my page down" and "and stop
    holding my documents" are different requests."""
    from django.core.files.base import ContentFile

    from apps.directory.models import Document, VerificationType

    document = Document.objects.create(
        practitioner=listed,
        type=VerificationType.IDENTITY,
        original_filename="passport.pdf",
        mime_type="application/pdf",
        size_bytes=1234,
        sha256="0" * 64,
    )
    document.file.save("passport.pdf", ContentFile(b"not really a passport"), save=True)
    assert document.delete_after is None

    client.post(reverse("dashboard:remove"), {"confirm": "REMOVE"}, follow=True)

    listed.refresh_from_db()
    document.refresh_from_db()

    assert listed.status == PublicationStatus.REMOVED
    assert document.delete_after is not None
    assert public_profile(client, listed).status_code == 404


# ---------------------------------------------------------------------------
# Payload helpers
#
# Full payloads, because a Django ModelForm treats an absent field as an empty
# submission — a partial POST would look like an edit that blanked everything and
# would make `changed_data` meaningless, which is the whole thing under test here.
# ---------------------------------------------------------------------------


def _profile_payload(practitioner, **overrides):
    payload = {
        "full_name": practitioner.full_name,
        "display_title": practitioner.display_title,
        "post_nominals": practitioner.post_nominals,
        "pronouns": practitioner.pronouns,
        "gender": practitioner.gender,
        "profession": practitioner.profession_id or "",
        "years_experience": practitioner.years_experience or "",
        "qualified_since": practitioner.qualified_since or "",
        "intro": practitioner.intro,
        "services": practitioner.services,
        "public_email": practitioner.public_email,
        "public_phone": practitioner.public_phone,
        "public_website": practitioner.public_website,
    }
    payload.update(overrides)
    return payload


def _availability_payload(practitioner, **overrides):
    payload = {
        "delivery_mode": practitioner.delivery_mode,
        "online_coverage": practitioner.online_coverage,
        "typical_wait": practitioner.typical_wait,
        "availability_note": practitioner.availability_note,
        "fee_min": (practitioner.fee_min or 0) / 100,
        "fee_max": (practitioner.fee_max / 100) if practitioner.fee_max else "",
    }
    for name in (
        "offers_online",
        "accepting_new_clients",
        "evening_appointments",
        "weekend_appointments",
        "free_initial_call",
        "offers_sliding_scale",
    ):
        if getattr(practitioner, name):
            payload[name] = "on"
    payload.update(overrides)
    return {k: v for k, v in payload.items() if v != ""}


def _credentials_payload(practitioner, **overrides):
    registrations = list(practitioner.registrations.all())
    qualifications = list(practitioner.qualifications.all())

    payload = {
        "registrations-TOTAL_FORMS": str(len(registrations) + 1),
        "registrations-INITIAL_FORMS": str(len(registrations)),
        "registrations-MIN_NUM_FORMS": "0",
        "registrations-MAX_NUM_FORMS": "1000",
        "qualifications-TOTAL_FORMS": str(len(qualifications) + 1),
        "qualifications-INITIAL_FORMS": str(len(qualifications)),
        "qualifications-MIN_NUM_FORMS": "0",
        "qualifications-MAX_NUM_FORMS": "1000",
    }

    for index, registration in enumerate(registrations):
        payload[f"registrations-{index}-id"] = str(registration.pk)
        payload[f"registrations-{index}-practitioner"] = str(practitioner.pk)
        payload[f"registrations-{index}-body"] = registration.body
        # The change under test: a different registration number.
        payload[f"registrations-{index}-registration_no"] = "7654321"
        payload[f"registrations-{index}-register_url"] = registration.register_url

    for index, qualification in enumerate(qualifications):
        payload[f"qualifications-{index}-id"] = str(qualification.pk)
        payload[f"qualifications-{index}-practitioner"] = str(practitioner.pk)
        payload[f"qualifications-{index}-title"] = qualification.title
        payload[f"qualifications-{index}-institution"] = qualification.institution
        payload[f"qualifications-{index}-year"] = qualification.year or ""

    payload.update(overrides)
    return payload
