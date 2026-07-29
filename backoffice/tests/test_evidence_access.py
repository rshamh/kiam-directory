"""Evidence access. Every open is logged, and there is no way round it.

"No exceptions, no debug bypass" is the requirement, so these tests attack the
ways a bypass usually appears: a template reaching for `.url`, a signed link
outliving its TTL, a link shared with someone who should not have it, and DEBUG
being on.
"""

from __future__ import annotations

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse
from django_otp.plugins.otp_totp.models import TOTPDevice

from directory.factories import PractitionerFactory
from directory.models import Document, DocumentAccessLog, VerificationType
from directory.services import documents as evidence
from directory.storages import EvidenceNotPublic

pytestmark = pytest.mark.django_db


@pytest.fixture
def document():
    practitioner = PractitionerFactory()
    doc = Document(
        practitioner=practitioner,
        type=VerificationType.DBS,
        original_filename="dbs-certificate.pdf",
        mime_type="application/pdf",
        size_bytes=1234,
        sha256="0" * 64,
    )
    doc.file.save("dbs.pdf", ContentFile(b"%PDF-1.4 evidence"), save=True)
    return doc


def _staff_client(client, user):
    """A signed-in staff session that has cleared the TOTP challenge."""
    device = TOTPDevice.objects.create(user=user, confirmed=True, name="default")
    client.force_login(user)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


# ---------------------------------------------------------------------------
# The file has no public URL, ever
# ---------------------------------------------------------------------------


def test_the_file_field_uses_the_private_backend(document):
    """The Phase 1 fix, asserted from the model side."""
    assert document.file.storage.is_private_evidence is True


def test_asking_the_file_for_a_url_raises(document):
    """`{{ document.file.url }}` in a template is the accident this prevents."""
    with pytest.raises(EvidenceNotPublic):
        _ = document.file.url


# ---------------------------------------------------------------------------
# open_evidence — the only door
# ---------------------------------------------------------------------------


def test_opening_evidence_writes_an_access_log_row(document, verifier):
    assert DocumentAccessLog.objects.count() == 0

    evidence.open_evidence(document, user=verifier, ip="203.0.113.9", reason="DBS check")

    entry = DocumentAccessLog.objects.get()
    assert entry.document == document
    assert entry.user == verifier
    assert entry.ip == "203.0.113.9"


def test_every_open_writes_its_own_row(document, verifier):
    for _ in range(3):
        evidence.open_evidence(document, user=verifier)

    assert DocumentAccessLog.objects.filter(document=document).count() == 3


def test_an_admin_cannot_open_evidence(document, admin_user):
    """The admin/verifier split at the point it actually matters."""
    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.open_evidence(document, user=admin_user)

    assert DocumentAccessLog.objects.count() == 0


def test_a_practitioner_cannot_open_evidence(document, practitioner):
    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.open_evidence(document, user=practitioner)


def test_anonymous_cannot_open_evidence(document):
    from django.contrib.auth.models import AnonymousUser

    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.open_evidence(document, user=AnonymousUser())


def test_a_refused_open_writes_no_access_row_but_is_logged(document, admin_user, caplog):
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="directory.documents"),
        pytest.raises(evidence.EvidenceAccessDenied),
    ):
        evidence.open_evidence(document, user=admin_user)

    assert DocumentAccessLog.objects.count() == 0
    assert "evidence.access_denied" in [r.getMessage() for r in caplog.records]


def test_debug_mode_is_not_a_bypass(document, admin_user, settings):
    """DEBUG=True must not open a side door onto somebody's DBS certificate."""
    settings.DEBUG = True

    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.open_evidence(document, user=admin_user)


# ---------------------------------------------------------------------------
# The streaming link
# ---------------------------------------------------------------------------


def test_the_minted_link_is_not_a_media_path(document, verifier):
    url = evidence.open_evidence(document, user=verifier)

    assert "/media/" not in url
    assert url.startswith("/backoffice/evidence/stream/")


def test_a_valid_token_resolves_to_its_document(document, verifier):
    url = evidence.open_evidence(document, user=verifier)
    token = url.rstrip("/").rsplit("/", 1)[1]

    assert evidence.verify_stream_token(token).pk == document.pk


def test_an_expired_token_is_refused(document, verifier):
    url = evidence.open_evidence(document, user=verifier)
    token = url.rstrip("/").rsplit("/", 1)[1]

    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.verify_stream_token(token, max_age=-1)


def test_a_tampered_token_is_refused(document, verifier):
    url = evidence.open_evidence(document, user=verifier)
    token = url.rstrip("/").rsplit("/", 1)[1]

    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.verify_stream_token(token + "x")


def test_a_token_from_another_signer_is_refused(document):
    """The salt namespaces it, so a signed value from elsewhere cannot be replayed."""
    from django.core import signing

    foreign = signing.TimestampSigner(salt="something.else").sign(str(document.pk))

    with pytest.raises(evidence.EvidenceAccessDenied):
        evidence.verify_stream_token(foreign)


# ---------------------------------------------------------------------------
# Through the actual HTTP surface
# ---------------------------------------------------------------------------


def test_a_verifier_can_open_and_stream_evidence(client, document, verifier):
    staff = _staff_client(client, verifier)

    response = staff.post(reverse("backoffice:evidence_open", kwargs={"document_id": document.pk}))
    assert response.status_code == 302
    assert DocumentAccessLog.objects.count() == 1

    streamed = staff.get(response.url)
    assert streamed.status_code == 200
    assert b"evidence" in b"".join(streamed.streaming_content)
    assert "no-store" in streamed["Cache-Control"]


def test_an_admin_gets_403_from_the_open_view(client, document, admin_user):
    staff = _staff_client(client, admin_user)

    response = staff.post(reverse("backoffice:evidence_open", kwargs={"document_id": document.pk}))

    assert response.status_code == 403
    assert DocumentAccessLog.objects.count() == 0


def test_the_open_view_refuses_GET(client, document, verifier):
    """A GET that mutates would be fetched by every prefetcher and preview bot.

    Opening evidence writes a DocumentAccessLog row, so it is a POST.
    """
    staff = _staff_client(client, verifier)

    response = staff.get(reverse("backoffice:evidence_open", kwargs={"document_id": document.pk}))

    assert response.status_code == 405
    assert DocumentAccessLog.objects.count() == 0


def test_a_valid_link_handed_to_the_wrong_person_still_fails(client, document, verifier, admin_user):
    """The signature proves the link was minted legitimately, not that the person
    holding it now is entitled to use it."""
    url = evidence.open_evidence(document, user=verifier)

    staff = _staff_client(client, admin_user)
    response = staff.get(url)

    assert response.status_code == 403


def test_an_anonymous_visitor_cannot_stream(client, document, verifier):
    url = evidence.open_evidence(document, user=verifier)

    response = client.get(url)

    assert response.status_code in (302, 403)


def test_the_access_log_is_append_only_in_admin():
    from django.contrib import admin as django_admin

    from directory.models import DocumentAccessLog as Model

    model_admin = django_admin.site._registry[Model]

    assert model_admin.has_add_permission(None) is False
    assert model_admin.has_change_permission(None) is False
    assert model_admin.has_delete_permission(None) is False


# ---------------------------------------------------------------------------
# Permissions across the whole back office
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url_name",
    [
        "backoffice:dashboard",
        "backoffice:review_queue",
        "backoffice:practitioner_list",
        "backoffice:invite_list",
        "backoffice:concern_queue",
        "backoffice:audit_log",
    ],
)
def test_a_practitioner_cannot_reach_the_back_office(client, practitioner, url_name):
    client.force_login(practitioner)

    response = client.get(reverse(url_name))

    assert response.status_code == 403


@pytest.mark.parametrize(
    "url_name",
    ["backoffice:dashboard", "backoffice:review_queue", "backoffice:audit_log"],
)
def test_anonymous_visitors_cannot_reach_the_back_office(client, url_name):
    response = client.get(reverse(url_name))
    assert response.status_code in (302, 403)


def test_an_unverified_staff_session_is_sent_to_the_totp_challenge(client, admin_user):
    """Phase 0's middleware still gates every back-office page."""
    client.force_login(admin_user)

    response = client.get(reverse("backoffice:dashboard"))

    assert response.status_code == 302
    assert "/accounts/two-factor/" in response.url
