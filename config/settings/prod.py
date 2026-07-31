"""Production.

Every value that must not be guessed is read from the environment with no
default, so a missing one fails the boot rather than silently running insecurely.
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

SECRET_KEY = env("SECRET_KEY")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
# The three Kiam subdomains are separate deploys with separate certificates and
# are not all guaranteed to be HTTPS-only at the same moment. Preloading, or
# claiming includeSubDomains from a subdomain, is not this project's to assert.
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)

# ---------------------------------------------------------------------------
# Object storage — the two backends stay separate here too
# ---------------------------------------------------------------------------
# `default` is public-read: headshots, CDN-able. `private` is a distinct bucket
# with no public read policy; its objects are reachable only through a presigned
# URL minted by directory.services.documents.signed_url. Two buckets rather than
# two prefixes, because a prefix is one bucket-policy edit away from being world
# readable and a separate bucket is not.

_AWS_COMMON = {
    "access_key": env("AWS_ACCESS_KEY_ID", default=""),
    "secret_key": env("AWS_SECRET_ACCESS_KEY", default=""),
    "region_name": env("AWS_S3_REGION_NAME", default=""),
    "endpoint_url": env("AWS_S3_ENDPOINT_URL", default=None),
}

if env("PUBLIC_MEDIA_BUCKET", default=""):
    STORAGES = {  # noqa: F405
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                **_AWS_COMMON,
                "bucket_name": env("PUBLIC_MEDIA_BUCKET"),
                "default_acl": "public-read",
                "querystring_auth": False,
                "file_overwrite": False,
            },
        },
        "private": {
            "BACKEND": "apps.directory.storages.PrivateEvidenceS3Storage",
            "OPTIONS": {
                **_AWS_COMMON,
                "bucket_name": env("PRIVATE_EVIDENCE_BUCKET"),
                # No ACL, signed URLs only, and they expire. The storage class
                # itself re-asserts all three — see directory/storages.py.
                "default_acl": None,
                "querystring_auth": True,
                "querystring_expire": env.int("PRIVATE_DOCUMENT_URL_TTL_SECONDS", default=300),
                "file_overwrite": False,
            },
        },
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# ---------------------------------------------------------------------------
# Sentry — a no-op when SENTRY_DSN is unset
# ---------------------------------------------------------------------------

SENTRY_DSN = env("SENTRY_DSN")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=env.float("SENTRY_TRACES_SAMPLE_RATE", default=0.0),
        environment=env("SENTRY_ENVIRONMENT", default="production"),
        release=env("GIT_SHA", default=None),
        # GDPR: no PII to a third-party processor by default. A magic-link token
        # or an email address must never reach the error tracker.
        send_default_pii=False,
    )
