"""Test settings.

Still PostGIS: the geo column types and the GiST index are part of what the suite
has to exercise from Phase 1 onward, and a sqlite/spatialite stand-in would let a
migration pass here and fail on the real database.

The cache is local-memory rather than Redis so the rate-limit tests are
deterministic and need no running server; the code under test only ever talks to
``django.core.cache``, so this swaps cleanly.
"""

from .base import *  # noqa: F403
from .base import BASE_DIR, LOGGING, MIDDLEWARE, env

# WhiteNoise warns on every request that STATIC_ROOT does not exist, and it does
# not exist in a test run because nothing has collected static. The middleware
# adds nothing the suite tests, so it comes out rather than being worked around.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]

DEBUG = False

SECRET_KEY = "test-only-key-not-used-anywhere-else"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

SITE_BASE_URL = "https://directory.kiamclinic.test"

DATABASES = {
    "default": {
        **env.db(
            "DATABASE_URL",
            default="postgis://postgres:postgres@localhost:5432/kiam_directory",
        ),
        "ENGINE": "django.contrib.gis.db.backends.postgis",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "kiam-directory-tests",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# A Secure cookie is never set over the test client's plain HTTP, so the session
# tests would see an anonymous user after a successful login.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "private": {
        "BACKEND": "directory.storages.PrivateEvidenceStorage",
        "OPTIONS": {"location": str(BASE_DIR / ".pytest-private-media"), "base_url": None},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

LOGGING["root"]["level"] = "CRITICAL"
LOGGING["handlers"]["console"]["formatter"] = "plain"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
