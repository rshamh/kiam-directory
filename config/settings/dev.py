"""Local development.

Cookies are not marked Secure here because `runserver` is plain HTTP and a Secure
cookie is simply never sent — you would appear to log in and immediately be
anonymous. Every other environment keeps them Secure (see base.py / prod.py).
"""

from .base import *  # noqa: F403
from .base import BASE_DIR, INSTALLED_APPS, LOGGING, MIDDLEWARE, env

DEBUG = True

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "web", "[::1]"]
CSRF_TRUSTED_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000"]

SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# The magic link is printed to the console rather than posted anywhere.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

SITE_BASE_URL = env("SITE_BASE_URL", default="http://localhost:8000").rstrip("/")

# Human-readable logs locally; JSON is for the log shipper, not for a terminal.
LOGGING["handlers"]["console"]["formatter"] = "plain"

# django-debug-toolbar, only when it is actually installed.
try:
    import debug_toolbar  # noqa: F401
except ImportError:
    pass
else:
    INSTALLED_APPS += ["debug_toolbar"]
    MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")
    INTERNAL_IPS = ["127.0.0.1"]

# Evidence never leaves the box in development.
STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "private": {
        "BACKEND": "directory.storages.PrivateEvidenceStorage",
        "OPTIONS": {"location": str(BASE_DIR / "private-media"), "base_url": None},
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
