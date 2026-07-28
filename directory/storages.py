"""Two storage backends that are never merged.

``STORAGES["default"]`` is **public**: headshots, served directly, CDN-able.
``STORAGES["private"]`` is **verification evidence**: passport scans, DBS
certificates, registration documents. It has no public URL and never will.

The separation is enforced in code rather than trusted to convention, because the
failure is silent and unrecoverable. A ``FileField`` that forgets
``storage=private_storage`` writes a passport scan into the public bucket, the
upload succeeds, the page renders, and the only symptom is that a URL now exists
which anybody can fetch. Nothing errors.

So the classes below make the wrong thing raise:

* ``url()`` raises ``EvidenceNotPublic`` — the ONLY way to a private object is
  ``directory.services.documents.signed_url``, which logs the access.
* ``PrivateEvidenceS3Storage`` re-asserts "no ACL, signed URLs, and they expire"
  in ``__init__`` regardless of what settings passed, so a misconfigured
  ``default_acl`` in an environment file cannot quietly publish the bucket.

The reverse guard matters too: ``public_storage()`` refuses to hand back a
storage that is actually the private one, so a copy-paste in a future model
cannot route evidence through the public path.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
from django.core.files.storage import FileSystemStorage, storages


class EvidenceNotPublic(SuspiciousOperation):
    """Raised when something asks a private evidence file for a public URL."""


class _NoPublicURLMixin:
    """Shared refusal. ``url()`` is the one method that must never work here.

    ``url()`` is what a template calls — ``{{ document.file.url }}`` — and what a
    serialiser reaches for. Blocking exactly that name, and putting signing behind
    a differently-named method, means the accidental path raises and the
    deliberate one is impossible to type by mistake.
    """

    #: Read by ``public_storage()`` and by the tests. A backend carrying this flag
    #: may not be used for anything a template can render a link to.
    is_private_evidence = True

    #: Whether this backend can mint a time-limited URL at all.
    can_sign_urls = False

    def url(self, name):  # noqa: ARG002
        raise EvidenceNotPublic(
            "Verification evidence has no public URL. Use "
            "directory.services.documents.signed_url(), which mints a short-lived "
            "link and logs the access."
        )

    def signed_url(self, name, *, expire: int):
        """Mint a time-limited URL. Overridden by backends that can.

        Not public API — go through ``directory.services.documents.signed_url``,
        which is where the permission check and the access log live.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot sign URLs.")


class PrivateEvidenceStorage(_NoPublicURLMixin, FileSystemStorage):
    """Local private storage, for development and tests.

    ``base_url`` is forced to ``None``. Note that clearing the constructor
    argument is not enough: ``FileSystemStorage.base_url`` falls back to
    ``settings.MEDIA_URL`` when it is unset, so a private file would end up with
    a ``/media/…`` URL — served, in development, by the static/media handler.
    The property is overridden outright.
    """

    def __init__(self, *args, **kwargs):
        kwargs["base_url"] = None
        super().__init__(*args, **kwargs)

    @property
    def base_url(self):
        return None


try:  # pragma: no cover - exercised only where django-storages[s3] is installed
    from storages.backends.s3 import S3Storage
except ImportError:  # pragma: no cover
    S3Storage = None


if S3Storage is not None:

    class PrivateEvidenceS3Storage(_NoPublicURLMixin, S3Storage):
        """Private object storage.

        The three settings that keep the bucket private are re-asserted here
        rather than trusted to the environment. An ops change that sets
        ``default_acl = "public-read"`` on this backend is a data breach, and it
        would be a one-line diff in a file nobody reviews as security-critical.
        """

        can_sign_urls = True

        def __init__(self, **settings):
            settings["default_acl"] = None
            settings["querystring_auth"] = True
            settings["file_overwrite"] = False
            super().__init__(**settings)

        def signed_url(self, name, *, expire: int):
            """Presign via S3Storage's own ``url()``, bypassing the mixin's block.

            Reached only from ``directory.services.documents.signed_url``, which
            has already checked the capability and written the access log.
            """
            return S3Storage.url(self, name, expire=expire)

else:  # pragma: no cover

    class PrivateEvidenceS3Storage:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImproperlyConfigured(
                "PrivateEvidenceS3Storage needs django-storages[s3]; install the s3 extra."
            )


# ---------------------------------------------------------------------------
# Accessors — use these, not `storages[...]` directly
# ---------------------------------------------------------------------------


def private_storage():
    """The evidence backend. Every private ``FileField`` passes ``storage=`` this."""
    storage = storages["private"]
    if not getattr(storage, "is_private_evidence", False):
        raise ImproperlyConfigured(
            "STORAGES['private'] is not a private evidence backend. It must subclass "
            "directory.storages.PrivateEvidenceStorage or PrivateEvidenceS3Storage, "
            "which are the classes that refuse to produce a public URL."
        )
    return storage


def public_storage():
    """The public backend, for headshots.

    Guarded in the other direction: if ``default`` has somehow been pointed at the
    private backend, this raises rather than let a headshot land somewhere with no
    URL — and, more to the point, rather than let a future caller reach for
    ``public_storage()`` and get something it will then try to publish.
    """
    storage = storages["default"]
    if getattr(storage, "is_private_evidence", False):
        raise ImproperlyConfigured(
            "STORAGES['default'] is the private evidence backend. The public and "
            "private backends must never be the same store."
        )
    return storage
