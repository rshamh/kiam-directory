"""Signed access to private verification evidence.

Nothing uses this yet — the ``Document`` model and the verification workbench are
Phase 1 and Phase 2. It is written now because the *shape* of evidence access has
to be settled before anything stores a passport scan, and retrofitting "actually,
every read should have been logged" after the fact means a gap in the audit trail
that cannot be filled in.

The contract, in one place:

* A private object has **no URL**. ``PrivateEvidenceStorage.url()`` raises.
* The only way to a private object is ``signed_url()``, which mints a link with a
  short TTL (``PRIVATE_DOCUMENT_URL_TTL_SECONDS``, default 5 minutes).
* ``signed_url()`` takes the ``user`` doing the looking and refuses anyone who is
  not a verifier, via ``accounts.access.can_view_evidence``. ``admin`` is
  deliberately not enough (docs/architecture.md, "Roles").
* Every mint is logged. From Phase 2 that log line is joined by a
  ``DocumentAccessLog`` row; the hook is marked below.

The authored ``directory/models.py`` (Phase 1) names the caller that writes that
row ``directory.services.documents.open_evidence()``. That function is Phase 2's
to add and it belongs on top of this one, not instead of it: ``open_evidence()``
takes a ``Document``, writes the ``DocumentAccessLog`` row, and calls
``signed_url()`` for the link. Keeping the URL-minting separate is what lets the
permission check and the "no public URL" guarantee be tested without a Document
model existing yet.

Why a TTL of minutes: the link is handed to one verifier for one look. A signed
URL is a bearer credential — anyone who ends up with it can fetch the object —
so it should expire before it can be forwarded, indexed, or left in a browser
history that someone else reads.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import PermissionDenied

from accounts.access import can_view_evidence

from ..storages import private_storage

logger = logging.getLogger("directory.documents")


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

    # TODO(phase-2): write a DocumentAccessLog row here, in the same transaction
    # as the mint, so "who opened this, when, and why" survives log rotation.
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


def exists(name: str) -> bool:
    """Whether a private object is present. No permission implication."""
    return bool(name) and private_storage().exists(name)
