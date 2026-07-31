"""The rest of the dashboard: access, evidence, insights, security.

The gate's three demonstrations are in ``test_gate.py``. This is everything else
that would be a real problem if it were wrong.
"""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts.models import User
from directory.factories import PractitionerFactory
from directory.models import Document, VerificationStatus, VerificationType

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    return User.objects.create_user(email="owner@example.com", role=User.Role.PRACTITIONER)


@pytest.fixture
def listing(owner):
    return PractitionerFactory(published=True, complete=True, user=owner, slug="owner-listing")


@pytest.fixture
def signed_in(client, owner, listing):
    client.force_login(owner)
    return listing


def a_pdf(name="certificate.pdf", size=2048):
    return SimpleUploadedFile(name, b"%PDF-1.4\n" + b"0" * size, content_type="application/pdf")


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

PAGES = (
    "dashboard:home",
    "dashboard:profile",
    "dashboard:taxonomy",
    "dashboard:locations",
    "dashboard:credentials",
    "dashboard:availability",
    "dashboard:documents",
    "dashboard:insights",
    "dashboard:security",
    "dashboard:unpublish",
    "dashboard:remove",
)


@pytest.mark.parametrize("name", PAGES)
def test_every_page_needs_a_signed_in_practitioner(client, name):
    assert client.get(reverse(name)).status_code == 404


def test_no_staff_role_has_a_practitioner_dashboard(admin_user, verifier, superadmin):
    """Staff act on a listing through the back office, which has an audit trail and
    a review path. This UI has neither, so a staff account editing somebody's
    listing here would be invisible.

    Asserted on the predicate rather than over HTTP, because a staff session is
    intercepted by the two-factor middleware first and redirected to the challenge
    — a refusal, but a different one, and testing it that way would pass even if
    `can_use_dashboard` let staff through.
    """
    from accounts.access import can_use_dashboard

    for user in (admin_user, verifier, superadmin):
        assert can_use_dashboard(user) is False, f"{user.role} should have no dashboard"


@pytest.mark.parametrize("name", PAGES)
def test_a_practitioner_with_no_listing_gets_a_404(client, name):
    """A 404, not a 403: "you may not see this" tells somebody there is something
    there."""
    orphan = User.objects.create_user(email="orphan@example.com", role=User.Role.PRACTITIONER)
    client.force_login(orphan)

    assert client.get(reverse(name)).status_code == 404


@pytest.mark.parametrize("name", PAGES)
def test_a_signed_in_practitioner_sees_their_own(client, signed_in, name):
    assert client.get(reverse(name)).status_code == 200


def test_no_dashboard_url_names_a_practitioner():
    """The listing comes from the session, never from the URL.

    A structural assertion, because the obvious "improvement" — `/dashboard/<pk>/`
    so staff can help somebody — is one line away and would turn every view in this
    app into an authorisation decision that each one has to remember to make.
    """
    from dashboard import urls as dashboard_urls

    for pattern in dashboard_urls.urlpatterns:
        route = str(pattern.pattern)
        assert "practitioner" not in route, f"{route} takes a practitioner from the URL"
        assert "slug" not in route, f"{route} takes a slug from the URL"


def test_the_dashboard_is_noindex_and_never_cached(client, signed_in):
    response = client.get(reverse("dashboard:home"))

    assert b'name="robots" content="noindex, nofollow"' in response.content
    assert "no-store" in response["Cache-Control"] or "no-cache" in response["Cache-Control"]


def test_the_dashboard_is_disallowed_in_robots(client):
    assert "Disallow: /dashboard/" in client.get("/robots.txt").content.decode()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_an_upload_is_refused_when_no_scanner_is_configured(client, signed_in, settings):
    """Fails closed, and this is the default. A control that switches itself off
    when it cannot reach its dependency is not a control — the same reasoning as
    the POM dictionary, which raises rather than permitting everything."""
    settings.ANTIVIRUS_BACKEND = "reject"

    response = client.post(
        reverse("dashboard:documents"),
        {"check_type": "identity", "file": a_pdf()},
        follow=True,
    )

    assert Document.objects.count() == 0
    assert "no virus scanner is configured" in response.content.decode()


def test_production_settings_do_not_skip_the_virus_scan():
    """`skip` is development only, and prod must fall through to base's `reject`.

    Asserted on the source rather than by importing prod settings, which needs a
    SECRET_KEY and a bucket name in the environment.
    """
    from pathlib import Path

    prod = (Path(__file__).resolve().parents[2] / "config" / "settings" / "prod.py").read_text()
    assert "ANTIVIRUS_BACKEND" not in prod, "production must not choose its own scanner backend"

    base = (Path(__file__).resolve().parents[2] / "config" / "settings" / "base.py").read_text()
    assert 'ANTIVIRUS_BACKEND = env("ANTIVIRUS_BACKEND", default="reject")' in base


def test_a_clean_upload_is_stored_privately_and_marks_the_check_submitted(client, signed_in, settings):
    settings.ANTIVIRUS_BACKEND = "skip"

    response = client.post(
        reverse("dashboard:documents"),
        {"check_type": "insurance", "file": a_pdf("insurance.pdf")},
        follow=True,
    )
    assert response.status_code == 200

    document = Document.objects.get()
    assert document.practitioner_id == signed_in.pk
    assert document.original_filename == "insurance.pdf"
    assert document.sha256

    # Private storage: there is no public URL, by construction.
    from directory.storages import EvidenceNotPublic

    with pytest.raises(EvidenceNotPublic):
        document.file.url  # noqa: B018

    check = signed_in.verifications.get(type=VerificationType.INSURANCE)
    assert check.status == VerificationStatus.SUBMITTED


def test_an_upload_cannot_advance_the_practitioner_s_own_verification(client, signed_in, settings):
    """The one thing CLAUDE.md forbids outright. Uploading a replacement for an
    in-date certificate must not touch the badge in either direction — not off
    (that would punish keeping evidence current) and certainly not on."""
    settings.ANTIVIRUS_BACKEND = "skip"

    from django.utils import timezone

    from directory.models import VerificationCheck
    from directory.services import verification

    check, _ = VerificationCheck.objects.get_or_create(
        practitioner=signed_in, type=VerificationType.INSURANCE
    )
    check.status = VerificationStatus.VERIFIED
    check.checked_at = timezone.now()
    check.expires_at = timezone.now() + timezone.timedelta(days=200)
    check.save()
    verification.recompute(signed_in)
    signed_in.refresh_from_db()
    was_verified = signed_in.is_verified

    client.post(
        reverse("dashboard:documents"),
        {"check_type": "insurance", "file": a_pdf()},
        follow=True,
    )

    check.refresh_from_db()
    signed_in.refresh_from_db()
    assert check.status == VerificationStatus.VERIFIED
    assert signed_in.is_verified == was_verified


def test_a_wrong_file_type_is_refused_before_the_scanner_sees_it(client, signed_in, settings):
    settings.ANTIVIRUS_BACKEND = "skip"

    response = client.post(
        reverse("dashboard:documents"),
        {
            "check_type": "identity",
            "file": SimpleUploadedFile("evidence.zip", b"PK\x03\x04", content_type="application/zip"),
        },
        follow=True,
    )

    assert Document.objects.count() == 0
    assert "export it as a PDF" in response.content.decode()


def test_an_oversized_file_is_refused(client, signed_in, settings):
    settings.ANTIVIRUS_BACKEND = "skip"

    from directory.services import antivirus

    big = SimpleUploadedFile(
        "huge.pdf", b"%PDF-1.4\n" + b"0" * (antivirus.MAX_BYTES + 1), content_type="application/pdf"
    )
    response = client.post(
        reverse("dashboard:documents"), {"check_type": "identity", "file": big}, follow=True
    )

    assert Document.objects.count() == 0
    assert "The limit is" in response.content.decode()


def test_a_verified_document_can_be_replaced_but_not_deleted(client, signed_in, settings):
    """The brief. A badge asserting that somebody's registration was checked, with
    the checked thing deleted at their request, leaves Kiam asserting something it
    can no longer show."""
    settings.ANTIVIRUS_BACKEND = "skip"

    client.post(reverse("dashboard:documents"), {"check_type": "identity", "file": a_pdf()}, follow=True)
    document = Document.objects.get()

    # Not yet checked: theirs to withdraw.
    from directory.services import documents as evidence

    assert evidence.can_delete(document) is True

    check = document.verification_check
    check.status = VerificationStatus.VERIFIED
    check.save(update_fields=["status"])
    document.refresh_from_db()

    assert evidence.can_delete(document) is False
    assert evidence.can_replace(document) is True

    response = client.post(reverse("dashboard:document_delete", args=[document.pk]), follow=True)
    assert Document.objects.filter(pk=document.pk).exists()
    assert "stays on file as the evidence" in response.content.decode()


def test_the_dashboard_offers_no_way_to_open_an_uploaded_document(client, signed_in, settings):
    """The only door to a private object is `documents.open_evidence()`, which
    requires `can_view_evidence` and logs. A "download your own copy" link would be
    a second, unlogged route to the evidence bucket."""
    settings.ANTIVIRUS_BACKEND = "skip"
    client.post(reverse("dashboard:documents"), {"check_type": "identity", "file": a_pdf()}, follow=True)

    body = client.get(reverse("dashboard:documents")).content.decode()

    assert "/media/evidence" not in body
    assert "evidence/open" not in body
    assert "evidence/stream" not in body


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


def test_insights_compare_against_the_previous_window(signed_in):
    from datetime import date, timedelta

    from dashboard.services import insights
    from directory.models import DailyMetric

    today = date(2026, 7, 30)
    DailyMetric.objects.create(practitioner=signed_in, date=today, profile_views=10)
    DailyMetric.objects.create(practitioner=signed_in, date=today - timedelta(days=40), profile_views=4)

    report = insights.build(signed_in, today=today)
    views = report.total("profile_views")

    assert views.value == 10
    assert views.previous == 4
    assert views.change == 6
    assert views.direction == "up"


def test_a_percentage_change_from_zero_is_not_invented(signed_in):
    from datetime import date

    from dashboard.services import insights
    from directory.models import DailyMetric

    today = date(2026, 7, 30)
    DailyMetric.objects.create(practitioner=signed_in, date=today, profile_views=3)

    views = insights.build(signed_in, today=today).total("profile_views")

    assert views.previous == 0
    assert views.change_percent is None, "0 -> 3 is not a percentage increase"


def test_the_funnel_names_the_two_different_problems(signed_in):
    from datetime import date

    from dashboard.services import insights
    from directory.models import DailyMetric

    today = date(2026, 7, 30)

    # Found but not opened.
    metric = DailyMetric.objects.create(
        practitioner=signed_in, date=today, search_impressions=400, profile_views=3
    )
    assert "not opening it" in insights.build(signed_in, today=today).funnel.diagnosis

    # Opened but nobody gets in touch.
    metric.search_impressions = 100
    metric.profile_views = 60
    metric.save()
    assert "not getting in touch" in insights.build(signed_in, today=today).funnel.diagnosis


def test_insights_say_what_the_numbers_are_not(client, signed_in):
    body = client.get(reverse("dashboard:insights")).content.decode()

    assert "not of people" in body
    assert "crawlers" in body


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


def test_changing_an_email_needs_both_addresses(client, signed_in, owner, mailoutbox):
    from accounts.services import email_change

    response = client.post(reverse("dashboard:security"), {"new_email": "new@example.com"}, follow=True)
    assert response.status_code == 200
    assert len(mailoutbox) == 2
    assert {m.to[0] for m in mailoutbox} == {"owner@example.com", "new@example.com"}

    request = email_change.open_request_for(owner)
    assert request is not None

    # One confirmation is not enough.
    tokens = _tokens_from(mailoutbox)
    email_change.confirm(tokens[0])
    owner.refresh_from_db()
    assert owner.email == "owner@example.com"

    # The second applies it.
    email_change.confirm(tokens[1])
    owner.refresh_from_db()
    assert owner.email == "new@example.com"


def test_the_old_address_is_told_after_the_fact(client, signed_in, owner, mailoutbox):
    from accounts.services import email_change

    client.post(reverse("dashboard:security"), {"new_email": "new@example.com"}, follow=True)
    for token in _tokens_from(mailoutbox):
        email_change.confirm(token)

    notices = [m for m in mailoutbox if "has been changed" in m.subject]
    assert notices, "the address that may have been taken must be told"
    assert notices[-1].to == ["owner@example.com"]


def test_an_email_cannot_be_moved_to_one_already_in_use(client, signed_in):
    User.objects.create_user(email="taken@example.com", role=User.Role.PRACTITIONER)

    response = client.post(reverse("dashboard:security"), {"new_email": "taken@example.com"})

    assert "already an account using that address" in response.content.decode()


def test_a_session_can_be_revoked_and_only_your_own(client, signed_in, owner):
    from accounts.models import UserSession
    from accounts.services import sessions

    # A second device.
    other = UserSession.objects.create(
        user=owner, session_key="a" * 32, user_agent="Mozilla/5.0 (iPhone) Safari/605"
    )
    stranger = User.objects.create_user(email="stranger@example.com", role=User.Role.PRACTITIONER)
    theirs = UserSession.objects.create(user=stranger, session_key="b" * 32)

    assert sessions.revoke(owner, theirs.session_key) is False, "another account's session"
    assert UserSession.objects.filter(pk=theirs.pk).exists()

    assert sessions.revoke(owner, other.session_key) is True
    assert not UserSession.objects.filter(pk=other.pk).exists()


def test_a_session_row_is_described_so_it_can_be_recognised(client, signed_in):
    from accounts.services import sessions

    assert sessions.describe("Mozilla/5.0 (Macintosh) Gecko Firefox/128.0") == "Firefox on macOS"
    assert sessions.describe("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5) Safari/604.1") == "Safari on iPhone"
    assert sessions.describe("") == "Unknown device"


def test_the_dashboard_offers_two_factor_to_a_practitioner(client, signed_in):
    """Phase 0 built the flow and gated it on a staff-only predicate, so there was
    no route for a practitioner to enrol at all."""
    body = client.get(reverse("dashboard:security")).content.decode()

    assert "Set up two-factor authentication" in body
    assert reverse("accounts:two_factor_setup") in body


def test_two_factor_cannot_be_turned_off_from_a_session_that_never_passed_it(client, signed_in, owner):
    """A security property, found by writing the test the obvious way round.

    Enrolling makes `must_challenge_two_factor` true, so the middleware sends this
    POST to the challenge before the view ever runs. That is what stops somebody
    who finds an unattended, half-authenticated session simply switching the second
    factor off — which would make it worth nothing.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice

    TOTPDevice.objects.create(user=owner, name="default", confirmed=True)
    owner.totp_enabled = True
    owner.save(update_fields=["totp_enabled"])

    response = client.post(reverse("dashboard:disable_two_factor"))

    assert response.status_code == 302
    assert response["Location"] == reverse("accounts:two_factor_verify")

    owner.refresh_from_db()
    assert owner.totp_enabled is True
    assert TOTPDevice.objects.filter(user=owner).exists()


def test_a_practitioner_may_turn_their_own_two_factor_off_and_staff_may_not(owner, admin_user):
    """It was optional when they turned it on, so it stays optional — an option you
    cannot reverse is a trap. Staff may not: their requirement belongs to the role."""
    from django_otp.plugins.otp_totp.models import TOTPDevice

    from accounts.access import can_disable_two_factor
    from accounts.services import two_factor

    TOTPDevice.objects.create(user=owner, name="default", confirmed=True)
    owner.totp_enabled = True
    owner.save(update_fields=["totp_enabled"])

    assert can_disable_two_factor(admin_user) is False
    assert can_disable_two_factor(owner) is True

    assert two_factor.disable(owner) is True
    owner.refresh_from_db()
    assert owner.totp_enabled is False
    assert not TOTPDevice.objects.filter(user=owner).exists()


def _tokens_from(mailoutbox):
    import re

    tokens = []
    for message in mailoutbox:
        found = re.findall(r"/accounts/email/confirm/([^/\s]+)/", message.body)
        tokens.extend(found)
    return tokens


# ---------------------------------------------------------------------------
# Taxonomy requests
# ---------------------------------------------------------------------------


def test_an_other_box_writes_a_taxonomy_request_not_a_listing_term(client, signed_in):
    """Free text here would produce a listing nobody can search for, so it becomes
    a request an admin triages into the controlled vocabulary."""
    from directory.models import TaxonomyRequest

    payload = {
        "specialities": [str(s.pk) for s in signed_in.specialities.all()],
        "approaches": [str(a.pk) for a in signed_in.approaches.all()],
        "client_groups": [str(g.pk) for g in signed_in.client_groups.all()],
        "languages": [str(lang.pk) for lang in signed_in.languages.all()],
        "other_speciality": "Perinatal OCD",
    }
    response = client.post(reverse("dashboard:taxonomy"), payload, follow=True)

    request = TaxonomyRequest.objects.get()
    assert request.axis == "speciality"
    assert request.proposed_term == "Perinatal OCD"
    assert "will not appear on your listing until we do" in response.content.decode()


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------


def test_the_meter_links_to_the_editor_that_fixes_each_gap(client, owner):
    """A percentage on its own is a guilt-trip. The number is only useful because
    the next click is beside it."""
    PractitionerFactory(user=owner, slug="bare-listing")
    client.force_login(owner)

    body = client.get(reverse("dashboard:home")).content.decode()

    assert "How complete your listing is" in body
    assert "Write a short introduction" in body
    assert reverse("dashboard:profile") in body


def test_weights_sum_to_one_hundred():
    """Adding a requirement has to be a deliberate decision about what it is worth
    relative to everything else, not a quiet rescaling of every listing's meter."""
    from directory.services import completeness

    assert sum(completeness.weights().values()) == 100


def test_every_publication_status_has_plain_language():
    """A missing key renders an empty panel on the one page that answers "is my
    listing live?"."""
    from dashboard.services.overview import STATUS_COPY
    from directory.models import PublicationStatus

    assert set(STATUS_COPY) == set(PublicationStatus.values)
