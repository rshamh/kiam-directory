"""The two storage backends, and the guard on private evidence.

Every test here is about a failure that would be silent in production: an upload
that succeeds, a page that renders, and a passport scan that is now fetchable by
anyone who guesses the URL.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.files.base import ContentFile

from directory import storages as dir_storages
from directory.services import documents

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The backends are distinct
# ---------------------------------------------------------------------------


def test_public_and_private_are_not_the_same_store():
    assert dir_storages.public_storage() is not dir_storages.private_storage()


def test_the_private_backend_is_flagged_as_private():
    assert dir_storages.private_storage().is_private_evidence is True


def test_the_public_backend_is_not_flagged_as_private():
    assert getattr(dir_storages.public_storage(), "is_private_evidence", False) is False


def test_public_storage_refuses_to_return_the_private_backend(settings):
    """A copy-paste that points `default` at the private store must fail loudly."""
    settings.STORAGES = {
        **settings.STORAGES,
        "default": settings.STORAGES["private"],
    }
    with pytest.raises(ImproperlyConfigured):
        dir_storages.public_storage()


def test_private_storage_refuses_a_backend_that_is_not_a_guarded_one(settings):
    settings.STORAGES = {
        **settings.STORAGES,
        "private": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }
    with pytest.raises(ImproperlyConfigured):
        dir_storages.private_storage()


# ---------------------------------------------------------------------------
# Private evidence has no public URL
# ---------------------------------------------------------------------------


def test_url_on_private_evidence_raises():
    """`{{ document.file.url }}` in a template is the accident this prevents."""
    storage = dir_storages.private_storage()
    storage.save("dbs/test.pdf", ContentFile(b"evidence"))

    with pytest.raises(dir_storages.EvidenceNotPublic):
        storage.url("dbs/test.pdf")


def test_the_private_filesystem_backend_has_no_base_url():
    """A base_url would make the file fetchable from MEDIA_URL in development."""
    assert dir_storages.private_storage().base_url is None


def test_the_local_private_backend_cannot_sign():
    assert dir_storages.private_storage().can_sign_urls is False


# ---------------------------------------------------------------------------
# signed_url — the only route in
# ---------------------------------------------------------------------------


def test_signed_url_refuses_a_practitioner(practitioner):
    with pytest.raises(PermissionDenied):
        documents.signed_url("dbs/test.pdf", user=practitioner)


def test_signed_url_refuses_an_admin(admin_user):
    """The admin/verifier split, asserted at the storage boundary too."""
    with pytest.raises(documents.EvidenceAccessDenied):
        documents.signed_url("dbs/test.pdf", user=admin_user)


def test_signed_url_refuses_anonymous():
    from django.contrib.auth.models import AnonymousUser

    with pytest.raises(documents.EvidenceAccessDenied):
        documents.signed_url("dbs/test.pdf", user=AnonymousUser())


def test_signed_url_rejects_an_empty_key(verifier):
    with pytest.raises(ValueError):
        documents.signed_url("", user=verifier)


def test_signed_url_refuses_to_fake_it_on_a_backend_that_cannot_sign(verifier):
    """Returning a /media/ path here would be the exact leak this module prevents."""
    with pytest.raises(NotImplementedError):
        documents.signed_url("dbs/test.pdf", user=verifier)


def test_a_permitted_access_is_logged(verifier, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="directory.documents"), pytest.raises(NotImplementedError):
        documents.signed_url("dbs/test.pdf", user=verifier, reason="DBS check")

    messages = [r.getMessage() for r in caplog.records]
    assert "evidence.url_signed" in messages


def test_a_refused_access_is_logged(admin_user, caplog):
    """An admin repeatedly reaching for evidence is something compliance sees."""
    import logging

    with (
        caplog.at_level(logging.WARNING, logger="directory.documents"),
        pytest.raises(documents.EvidenceAccessDenied),
    ):
        documents.signed_url("dbs/test.pdf", user=admin_user)

    assert "evidence.access_denied" in [r.getMessage() for r in caplog.records]


def test_signing_works_on_a_backend_that_can(verifier, monkeypatch):

    class FakeSigning:
        is_private_evidence = True
        can_sign_urls = True

        def signed_url(self, name, *, expire):
            return f"https://evidence.example/{name}?X-Amz-Expires={expire}"

    monkeypatch.setattr(dir_storages, "private_storage", lambda: FakeSigning())
    monkeypatch.setattr(documents, "private_storage", lambda: FakeSigning())

    url = documents.signed_url("dbs/test.pdf", user=verifier, ttl_seconds=60)
    assert url == "https://evidence.example/dbs/test.pdf?X-Amz-Expires=60"


def test_the_default_ttl_is_short(settings):
    """A signed URL is a bearer credential. Minutes, not hours."""
    assert documents.default_ttl() <= 900
