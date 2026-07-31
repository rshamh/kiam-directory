"""What the Phase 6 gate reviews found, pinned so it cannot come back.

Five blockers between the compliance and SEO reviewers, and none of them was
visible to a green suite of 1037 tests. Each one gets a test that fails if the fix
is removed, and the docstrings say what the hole was rather than what the code
does — the code is readable; the reason it exists is not.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django.urls import reverse

from apps.accounts.models import User
from apps.backoffice.services import review
from apps.directory.factories import (
    PractitionerFactory,
    PractitionerLocationFactory,
    ProfessionFactory,
    SpecialityFactory,
)
from apps.directory.models import (
    PublicationStatus,
    Registration,
    VerificationStatus,
    VerificationType,
)
from apps.directory.services import lint
from apps.pages.services import home

pytestmark = pytest.mark.django_db


@pytest.fixture
def listed(client):
    """A published, badged practitioner signed in to their own dashboard."""
    user = User.objects.create_user(email="listed@example.com", role=User.Role.PRACTITIONER)
    practitioner = PractitionerFactory(
        published=True, slug="listed-one", complete=True, verified=True, user=user
    )
    practitioner.refresh_from_db()
    assert practitioner.is_verified, "the fixture needs a badge for the withdrawal to be visible"

    client.force_login(user)
    return practitioner


# ---------------------------------------------------------------------------
# Compliance blockers 1 and 2 — the publication gate had no call site here
# ---------------------------------------------------------------------------


def test_a_practitioner_cannot_move_their_live_listing_to_a_title_they_cannot_hold(client, listed):
    """docs/content-compliance.md §3, "enforced at review, not just in the UI".

    `blocking_publication_reasons()` ran only at `approve()` and
    `lift_suspension()` — both staff transitions. The dashboard let a practitioner
    change `profession` to a restricted title with no verified registration behind
    it, and `submit_update()` correctly kept the listing PUBLISHED (right for a
    surname, wrong for a protected title), so the title rendered on the public page
    and in the JSON-LD `jobTitle`.
    """
    restricted = ProfessionFactory(restricted_title=True)
    assert not listed.registrations.filter(verified=True, body="GMC").exists()

    response = client.post(
        reverse("dashboard:profile"),
        _profile_payload(listed, profession=restricted.pk),
    )

    listed.refresh_from_db()
    assert response.status_code == 200, "the edit must be refused, not redirected"
    assert listed.profession_id != restricted.pk, "nothing may be saved"
    assert "restricted title" in response.content.decode()
    assert review.blocking_publication_reasons(listed) == []


def test_a_practitioner_cannot_delete_the_registration_their_title_rests_on(client, listed):
    """The second door to the same place, and it needed the same fix rather than a
    second predicate: the gate asks the authoritative function about the actual end
    state, so a third route cannot slip past it either."""
    listed.profession = ProfessionFactory(restricted_title=True)
    listed.save()
    Registration.objects.create(practitioner=listed, body="GMC", registration_no="1234567", verified=True)
    assert review.blocking_publication_reasons(listed) == []

    registration = listed.registrations.get(body="GMC")
    response = client.post(
        reverse("dashboard:credentials"), _credentials_payload(listed, delete=registration)
    )

    listed.refresh_from_db()
    assert response.status_code == 200
    assert listed.registrations.filter(pk=registration.pk).exists(), "the delete must roll back"
    assert "restricted title" in response.content.decode()


def test_the_formsets_are_saved_inside_the_transaction(client, listed):
    """They were saved by the view before `editing.save()` ran, so a credential
    change committed even when the rest of the edit failed — exactly the state
    `submit_update()` exists to prevent."""
    import inspect

    from apps.dashboard import views
    from apps.dashboard.services import editing

    source = inspect.getsource(views.credentials)
    assert "formsets=(qualifications, registrations)" in source
    assert "qualifications.save()" not in source
    assert "formsets" in inspect.signature(editing.save).parameters


# ---------------------------------------------------------------------------
# Compliance blocker 3 — POM names on related models the lint never read
# ---------------------------------------------------------------------------


def test_a_prescription_only_name_cannot_reach_a_practice_address(client, listed):
    """`LINTED_FIELDS` reads attributes off the Practitioner row, so a location
    labelled "methylphenidate clinic" was never scanned — and locations are a SAFE
    field, so it published immediately. Both render on the public profile."""
    location = PractitionerLocationFactory(practitioner=listed)
    term = lint.pom_terms()[0]

    response = client.post(
        reverse("dashboard:location_edit", args=[location.pk]),
        _location_payload(location, label=f"{term} clinic"),
    )

    location.refresh_from_db()
    assert response.status_code == 200, "the edit must be refused"
    assert term.lower() not in location.label.lower()


def test_the_lint_reads_related_model_free_text(listed):
    """The rule itself, not just the form that calls it — `review.submit()` has to
    see these too, or a draft carries one through to publication."""
    term = lint.pom_terms()[0]
    PractitionerLocationFactory(practitioner=listed, label=f"{term} clinic")

    result = lint.run(listed)

    assert result.is_blocked
    assert any(f.rule == "pom" and f.field == "label" for f in result.blocks)


# ---------------------------------------------------------------------------
# SEO blocker 1 — a stale cache could republish ungated child work
# ---------------------------------------------------------------------------


def test_a_speciality_edit_cannot_leave_child_work_on_the_cached_home_page(client, listed):
    """Phase 5 filtered ungated `implies_minors` specialities when the twelve were
    SELECTED. That was sufficient while only staff could change a live listing's
    specialities. The dashboard made it self-service and immediate, so a
    practitioner already inside the cached twelve could tick "Child & adolescent
    ADHD assessment" and have the pill render on the home page until the entry
    expired — up to 24 hours, under Kiam's own editorial selection.

    Two fixes, and this asserts the one that does not depend on the other working:
    hydration re-applies the exclusion, so the cache-bust is not load-bearing.
    """
    child = SpecialityFactory(slug="child-adhd", name="Child & adolescent ADHD", implies_minors=True)

    cache.delete("homepage:grid")
    assert listed.pk in {p.pk for p in home.grid()}, "must start in the cached twelve"

    # Straight to the relation, so the cache is deliberately NOT busted.
    listed.specialities.add(child)

    assert listed.pk not in {p.pk for p in home.grid()}


def test_a_dashboard_edit_busts_the_caches_that_can_hold_the_listing(client, listed):
    """The primary mechanism. `bust_cache` was only ever reached from staff
    transitions, which was enough before practitioners could edit a live listing."""
    cache.delete("homepage:grid")
    home.grid()
    assert cache.get("homepage:grid") is not None

    client.post(reverse("dashboard:profile"), _profile_payload(listed, intro=listed.intro + " Updated."))

    assert cache.get("homepage:grid") is None


# ---------------------------------------------------------------------------
# SEO blocker 2 — the email confirmation failed open under link prefetching
# ---------------------------------------------------------------------------


def test_fetching_the_email_confirmation_link_confirms_nothing(client, listed):
    """Corporate mail gateways fetch every URL in a message to scan it. The GET
    used to record the confirmation, so with both mailboxes behind such a gateway
    the sign-in credential moved with zero human action — defeating the control the
    module exists for.

    Note the asymmetry with the magic link, which is also a token-consuming GET: a
    prefetch there fails SAFE. This one failed OPEN.
    """
    from apps.accounts.services import email_change

    request = email_change.start(listed.user, "somewhere.else@example.com")
    raw = _raw_token_for(request, side="current")

    response = client.get(reverse("accounts:email_change_confirm", args=[raw]))

    request.refresh_from_db()
    assert response.status_code == 200
    assert request.confirmed_current_at is None, "a fetch must record nothing"
    assert "confirm this change" in response.content.decode().lower()


def test_the_confirmation_still_works_when_somebody_presses_the_button(client, listed):
    from apps.accounts.services import email_change

    request = email_change.start(listed.user, "somewhere.else@example.com")
    raw = _raw_token_for(request, side="current")

    client.post(reverse("accounts:email_change_confirm", args=[raw]))

    request.refresh_from_db()
    assert request.confirmed_current_at is not None


# ---------------------------------------------------------------------------
# Warnings worth pinning
# ---------------------------------------------------------------------------


def test_the_dbs_upload_the_countdown_demands_is_actually_offered(client, listed):
    """The overview's provisional countdown says "Upload your DBS certificate" and
    links here; the form offered four types and DBS was not one of them. A
    PROVISIONAL practitioner could not do the thing their 56-day window demanded."""
    response = client.get(reverse("dashboard:documents"))
    body = response.content.decode()

    for value in (VerificationType.DBS, VerificationType.PRESCRIBER, VerificationType.ICO):
        assert f'value="{value}"' in body, f"{value} is not offered"


def test_a_verified_document_stays_deletable_only_if_it_was_never_checked(listed):
    """`can_delete()` read the check's CURRENT status, so any transition off
    VERIFIED unlocked it — a routine surname change reopens the identity check, and
    the nightly sweep expires certificates. Either one made the evidence the badge
    was granted against deletable by the person it was granted to."""
    from apps.directory.models import Document
    from apps.directory.services import documents as evidence

    check = listed.verifications.get(type=VerificationType.IDENTITY)
    document = Document.objects.create(
        practitioner=listed,
        verification_check=check,
        type=VerificationType.IDENTITY,
        original_filename="id.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        sha256="x" * 64,
    )
    assert evidence.can_delete(document) is False

    # What a surname change does.
    check.status = VerificationStatus.SUBMITTED
    check.save(update_fields=["status"])

    assert evidence.can_delete(document) is False, "it was checked once; that does not un-happen"


def test_editing_a_registration_number_clears_its_verified_flag(listed):
    """`Registration.verified` is what lets a protected title publish.
    `invalidate_for_changes()` reopens the VerificationCheck; this row is a
    separate record and nothing cleared it."""
    from apps.dashboard.forms import RegistrationForm

    registration = Registration.objects.create(
        practitioner=listed, body="GMC", registration_no="1234567", verified=True
    )
    form = RegistrationForm(
        {"body": "GMC", "registration_no": "7654321", "register_url": ""}, instance=registration
    )
    assert form.is_valid(), form.errors
    form.save()

    registration.refresh_from_db()
    assert registration.verified is False


def test_a_withdrawn_consent_blocks_republication(listed, admin_user):
    """`withdraw_consent()` closes every open ConsentRecord, and nothing checked for
    one on the way back — so whatever staff improvised would have republished
    personal data with every consent row carrying `withdrawn_at`."""
    from apps.directory.models import ConsentRecord

    ConsentRecord.objects.create(
        practitioner=listed,
        terms_version="listing-agreement-v1.0",
        consent_publish=True,
        consent_registered=True,
        consent_independent=True,
        consent_accurate=True,
        consent_removal_path=True,
        signed_name=listed.full_name,
    )
    assert review.blocking_publication_reasons(listed) == []

    listed.consents.update(withdrawn_at="2026-01-01T00:00:00Z")

    reasons = review.blocking_publication_reasons(listed)
    assert reasons and "withdrawn their consent" in reasons[0]


def test_held_wording_on_a_live_listing_raises_a_review(client, listed):
    """docs/content-compliance.md §2 and §4 both say flagged copy "holds in review
    rather than auto-publishing". `intro` is a SAFE field, so an efficacy claim went
    live immediately — under a message telling the practitioner a reviewer would
    check it, which nobody would have."""
    before = listed.review_requests.filter(outcome="").count()

    client.post(
        reverse("dashboard:profile"),
        _profile_payload(listed, intro=listed.intro + " This treatment is guaranteed to work."),
    )

    listed.refresh_from_db()
    assert listed.review_requests.filter(outcome="").count() == before + 1
    assert listed.status == PublicationStatus.PUBLISHED, "a hold does not take the page down"


def test_the_reversible_action_does_not_ask_for_the_irreversible_word(client, listed):
    """Typing REMOVE to do the thing whose whole message is "nothing is deleted"
    contradicts the page and trains people to type it without reading."""
    assert client.post(reverse("dashboard:unpublish"), {"confirm": "REMOVE"}).status_code == 200
    listed.refresh_from_db()
    assert listed.status == PublicationStatus.PUBLISHED

    client.post(reverse("dashboard:unpublish"), {"confirm": "TAKE DOWN"})
    listed.refresh_from_db()
    assert listed.status == PublicationStatus.UNPUBLISHED


def test_retention_deletes_evidence_the_dashboard_promised_to_delete(listed):
    """`Document.delete_after` was written by `request_removal()` and nothing acted
    on it, while `remove.html` told the data subject the documents are deleted
    automatically. A retention promise nobody keeps is worse than none."""
    from datetime import timedelta

    from django.core.management import call_command
    from django.utils import timezone

    from apps.directory.models import Document

    document = Document.objects.create(
        practitioner=listed,
        type=VerificationType.INSURANCE,
        original_filename="cert.pdf",
        mime_type="application/pdf",
        size_bytes=1,
        sha256="y" * 64,
        delete_after=timezone.now() - timedelta(days=1),
    )
    call_command("purge_expired_evidence", verbosity=0)

    from apps.directory.models import AuditLog

    assert not Document.objects.filter(pk=document.pk).exists()
    assert AuditLog.objects.filter(
        action="evidence.retention_deleted", entity_id=str(document.pk)
    ).exists(), "the record of the deletion has to outlive the document"


# ---------------------------------------------------------------------------
# Payload helpers — a ModelForm needs every field, or absent ones read as blank
# ---------------------------------------------------------------------------


def _profile_payload(practitioner, **overrides):
    from apps.dashboard.forms import ProfileForm

    form = ProfileForm(instance=practitioner)
    payload = {}
    for name in form.fields:
        # The file field is deliberately omitted: absent from a POST means "leave
        # the current one", and posting the FieldFile back would try to read a
        # file the factory only ever named.
        if name == "headshot":
            continue
        value = form[name].value()
        payload[name] = "" if value is None else value
    payload.update(overrides)
    return payload


def _credentials_payload(practitioner, *, delete=None):
    payload = {"is_prescriber": practitioner.is_prescriber}
    for prefix, rows in (
        ("qualifications", practitioner.qualifications.all()),
        ("registrations", practitioner.registrations.all()),
    ):
        payload[f"{prefix}-TOTAL_FORMS"] = str(len(rows))
        payload[f"{prefix}-INITIAL_FORMS"] = str(len(rows))
        payload[f"{prefix}-MIN_NUM_FORMS"] = "0"
        payload[f"{prefix}-MAX_NUM_FORMS"] = "1000"
        for index, row in enumerate(rows):
            payload[f"{prefix}-{index}-id"] = str(row.pk)
            payload[f"{prefix}-{index}-practitioner"] = str(practitioner.pk)
            if prefix == "qualifications":
                payload[f"{prefix}-{index}-title"] = row.title
                payload[f"{prefix}-{index}-institution"] = row.institution
                payload[f"{prefix}-{index}-year"] = row.year or ""
            else:
                payload[f"{prefix}-{index}-body"] = row.body
                payload[f"{prefix}-{index}-registration_no"] = row.registration_no
                payload[f"{prefix}-{index}-register_url"] = row.register_url
            if delete is not None and row.pk == delete.pk:
                payload[f"{prefix}-{index}-DELETE"] = "on"
    return payload


def _location_payload(location, **overrides):
    from apps.dashboard.forms import LocationForm

    form = LocationForm(instance=location)
    payload = {}
    for name in form.fields:
        value = form[name].value()
        payload[name] = "" if value in (None, False) else ("on" if value is True else value)
    payload.update(overrides)
    return payload


def _raw_token_for(request, *, side):
    """Re-mint is impossible (only hashes are stored), so read it from the email."""
    import re

    from django.core import mail

    index = 0 if side == "current" else 1
    body = mail.outbox[index].body
    return re.search(r"/accounts/email/confirm/([^/]+)/", body).group(1)
