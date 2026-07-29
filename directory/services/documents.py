"""Signed access to private verification evidence.

The contract, in one place:

* A private object has **no URL**. ``PrivateEvidenceStorage.url()`` raises.
* The only way to a private object is ``signed_url()``, which mints a link with a
  short TTL (``PRIVATE_DOCUMENT_URL_TTL_SECONDS``, default 5 minutes).
* ``signed_url()`` takes the ``user`` doing the looking and refuses anyone who is
  not a verifier, via ``accounts.access.can_view_evidence``. ``admin`` is
  deliberately not enough (docs/architecture.md, "Roles").
* **Every open writes a ``DocumentAccessLog`` row**, in the same transaction as
  the URL is minted. ``open_evidence()`` is the only entry point a human reaches;
  ``signed_url()`` is the lower-level primitive it uses, kept separate so the
  permission check and the no-public-URL guarantee can be tested independently.

There is no debug bypass and no "just this once" path. A verifier who needs to
look at a passport scan generates a log row, every time.

Why a TTL of minutes: the link is handed to one verifier for one look. A signed
URL is a bearer credential — anyone who ends up with it can fetch the object —
so it should expire before it can be forwarded, indexed, or left in a browser
history that someone else reads.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from accounts.access import can_view_evidence

from ..storages import private_storage

logger = logging.getLogger("directory.documents")

#: Namespace for the local streaming signature, so an evidence token cannot be
#: replayed against any other signed URL in the project.
EVIDENCE_SALT = "directory.evidence"


class EvidenceAccessDenied(PermissionDenied):
    """Raised when someone without the verifier capability asks for evidence."""


def default_ttl() -> int:
    return getattr(settings, "PRIVATE_DOCUMENT_URL_TTL_SECONDS", 300)


def signed_url(name: str, *, user, ttl_seconds: int | None = None, reason: str = "") -> str:
    """A short-lived URL for the private object at ``name``.

    Args:
        name: the storage key, as held on the (Phase 1) ``Document.file`` field.
        user: who is asking. Checked against ``can_view_evidence``.
        ttl_seconds: overrides ``PRIVATE_DOCUMENT_URL_TTL_SECONDS``. Keep it short.
        reason: free text recorded with the access — "DBS check", "registration
            re-verification". Phase 2's workbench passes the queue item.

    Raises:
        EvidenceAccessDenied: the user may not open evidence.
        ValueError: ``name`` is empty.
        NotImplementedError: the configured private backend cannot sign URLs
            (the local filesystem backend, in development — see below).
    """
    if not name:
        raise ValueError("signed_url() needs a storage key.")

    if not can_view_evidence(user):
        # Log the refusal too. An admin repeatedly reaching for evidence they
        # cannot open is something the compliance lead should be able to see.
        logger.warning(
            "evidence.access_denied",
            extra={"user_id": getattr(user, "pk", None), "object": name},
        )
        raise EvidenceAccessDenied("This account cannot open verification evidence.")

    storage = private_storage()
    ttl = default_ttl() if ttl_seconds is None else ttl_seconds

    # NOTE: this primitive does NOT write a DocumentAccessLog row — it takes a
    # storage key, not a Document, and is used for cases with no row to log
    # against. Human access goes through open_evidence(), which does.
    logger.info(
        "evidence.url_signed",
        extra={
            "user_id": user.pk,
            "object": name,
            "ttl_seconds": ttl,
            "reason": reason,
        },
    )

    # Note `storage.signed_url`, not `storage.url` — the latter raises on every
    # private backend by design, so a template or serialiser that reaches for the
    # ordinary URL cannot get one. The local filesystem backend cannot sign
    # anything, and pretending otherwise (by returning a /media/ path) would be
    # the exact leak this module exists to prevent, so it raises too; development
    # uses the Phase 2 streaming view, which re-checks the capability per request.
    if not getattr(storage, "can_sign_urls", False):
        raise NotImplementedError(
            f"{type(storage).__name__} cannot sign URLs. Configure an object-storage "
            "backend for STORAGES['private'], or use the Phase 2 streaming view, "
            "which re-checks can_view_evidence on every request."
        )

    return storage.signed_url(name, expire=ttl)


def open_evidence(document, *, user, ip: str | None = None, reason: str = "") -> str:
    """The ONE way a human gets at an evidence file. Logs, then mints a URL.

    Named in the authored ``directory/models.py``: ``DocumentAccessLog`` is
    "written by directory.services.documents.open_evidence()". This is it.

    The log row is written BEFORE the URL exists, and in the same transaction, so
    there is no ordering in which a link is handed out and the record of it is
    not. If the log write fails, nobody gets a URL.

    Two backends, one contract:

    * object storage — a presigned URL with a short TTL;
    * local filesystem — a signed, expiring path to the streaming view, which
      re-checks the capability on the way through. NOT a /media/ URL; the
      private backend has no public URL by construction.

    Args:
        document: the ``directory.Document`` being opened.
        user: who is opening it. Refused unless ``can_view_evidence``.
        ip: recorded for the DPIA audit trail.
        reason: free text — "DBS check", "quarterly re-verification".
    """
    from ..models import DocumentAccessLog

    if not can_view_evidence(user):
        logger.warning(
            "evidence.access_denied",
            extra={"user_id": getattr(user, "pk", None), "document_id": str(document.pk)},
        )
        raise EvidenceAccessDenied("This account cannot open verification evidence.")

    with transaction.atomic():
        DocumentAccessLog.objects.create(document=document, user=user, ip=ip)
        logger.info(
            "evidence.opened",
            extra={
                "user_id": user.pk,
                "document_id": str(document.pk),
                "practitioner_id": str(document.practitioner_id),
                "reason": reason,
            },
        )

        storage = private_storage()
        ttl = default_ttl()

        if getattr(storage, "can_sign_urls", False):
            return storage.signed_url(document.file.name, expire=ttl)
        return _local_stream_url(document, user)


def _local_stream_url(document, user) -> str:
    """A signed, expiring path to the streaming view.

    The signature carries the document id, **the user it was minted for**, and a
    timestamp. Binding the user is what stops a forwarded link being usable: the
    access log names whoever opened it, so a second person streaming the same URL
    inside the TTL would read a passport scan under someone else's name and leave
    the DPIA trail pointing at the wrong person.

    The view still re-checks the capability on top of this — a signature proves
    the link was minted legitimately, not that the holder is still entitled to it.
    """
    from django.core import signing
    from django.urls import reverse

    payload = f"{document.pk}:{user.pk}"
    token = signing.TimestampSigner(salt=EVIDENCE_SALT).sign(payload)
    return reverse("backoffice:evidence_stream", kwargs={"token": token})


def verify_stream_token(token: str, *, user=None, max_age: int | None = None):
    """The document a streaming token refers to, if it is still valid.

    Raises ``EvidenceAccessDenied`` on a bad signature, an expired one, or a
    token minted for somebody else — the caller must not be able to tell which,
    so the holder of a forwarded link learns nothing.
    """
    from django.core import signing

    from ..models import Document

    signer = signing.TimestampSigner(salt=EVIDENCE_SALT)
    try:
        payload = signer.unsign(token, max_age=max_age if max_age is not None else default_ttl())
    except signing.SignatureExpired as exc:
        raise EvidenceAccessDenied("This evidence link has expired.") from exc
    except signing.BadSignature as exc:
        raise EvidenceAccessDenied("This evidence link is not valid.") from exc

    document_id, _, minted_for = payload.partition(":")

    if user is not None and str(getattr(user, "pk", "")) != minted_for:
        logger.warning(
            "evidence.token_user_mismatch",
            extra={"user_id": getattr(user, "pk", None), "minted_for": minted_for},
        )
        raise EvidenceAccessDenied("This evidence link is not valid.")

    document = Document.objects.filter(pk=document_id).select_related("practitioner").first()
    if document is None:
        raise EvidenceAccessDenied("This evidence link is not valid.")
    return document


def exists(name: str) -> bool:
    """Whether a private object is present. No permission implication."""
    return bool(name) and private_storage().exists(name)
