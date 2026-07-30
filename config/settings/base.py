"""Settings shared by every environment.

Nothing here is a secret and nothing here reads a secret without a default that is
safe to run with. Environment-specific overrides live in ``dev``/``prod``/``test``;
values come from the environment via ``django-environ`` (``.env`` in development,
real environment variables in production).

This project is deliberately isolated from the main site and the room-rental app:
its own database, its own sessions, its own deploy stack. See
``docs/multi-project-architecture.md`` §1 and §4 — in particular, the session
cookie stays HOST-ONLY. Do not add ``SESSION_COOKIE_DOMAIN``.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    MAGIC_LINK_TTL_MINUTES=(int, 15),
    MAGIC_LINK_MAX_PER_EMAIL=(int, 5),
    MAGIC_LINK_MAX_PER_IP=(int, 20),
    MAGIC_LINK_WINDOW_SECONDS=(int, 3600),
    SENTRY_DSN=(str, ""),
    SENTRY_TRACES_SAMPLE_RATE=(float, 0.0),
    SITE_BASE_URL=(str, "https://directory.kiamclinic.com"),
    PRIVATE_DOCUMENT_URL_TTL_SECONDS=(int, 300),
)

# Read `.env` only where a file is meant to be the configuration source.
#
# In a container the ENVIRONMENT is the configuration. But the development
# Compose service bind-mounts the repo for live reload, which drags the
# developer's `.env` in with it — and django-environ then fills in every key
# Compose does *not* set from that file. Compose sets DATABASE_URL and REDIS_URL,
# so those are safe; everything else silently inherits somebody's laptop. That is
# how this was found: the container picked up a macOS Homebrew GDAL_LIBRARY_PATH
# and died on a .dylib it could never have had.
#
# Evaluated before `read_env`, so it can only be answered by the real
# environment. Compose sets it to False; a developer running `manage.py` never
# sets it and gets the file, as they should.
if env.bool("READ_DOT_ENV_FILE", default=True):
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="insecure-development-key-override-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

#: Absolute base for canonical URLs, magic links and sitemap entries. Canonicals
#: stay inside this subdomain — never point at kiamclinic.com (docs/seo.md).
SITE_BASE_URL = env("SITE_BASE_URL").rstrip("/")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    # GeoDjango. Requires GDAL and GEOS on the host — see README.md.
    "django.contrib.gis",
    # Needed for the CreateExtension migration operations and, from Phase 4, the
    # trigram lookups registered by this app. ArrayField and SearchVectorField
    # work without it; TrigramSimilarity does not.
    "django.contrib.postgres",
]

THIRD_PARTY_APPS = [
    "django_htmx",
    "django_otp",
    "django_otp.plugins.otp_totp",
    # The shared brand chrome. Ships no models and no migrations.
    "kiam_ui",
]

LOCAL_APPS = [
    "accounts",
    "directory",
    "search",
    "dashboard",
    "backoffice",
    "seo",
    "pages",
]

# LOCAL_APPS come FIRST, which is not the usual ordering and is deliberate.
# Django's get_commands() walks the app registry in REVERSE and lets later
# updates win, so the app listed EARLIER in INSTALLED_APPS wins a management-
# command name collision. That is what lets seo/management/commands/
# collectstatic.py override the one in django.contrib.staticfiles — see the
# docstring there for why it has to. None of these apps ships templates of its
# own (they all render from the project-level templates/ directory), so nothing
# else changes ordering-wise.
INSTALLED_APPS = LOCAL_APPS + DJANGO_APPS + THIRD_PARTY_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must sit after AuthenticationMiddleware: it decorates request.user with the
    # verified-device state that accounts.access reads for the 2FA predicates.
    "django_otp.middleware.OTPMiddleware",
    # Must sit after OTPMiddleware: it reads the verified-device state that
    # middleware attaches. Keeps a half-authenticated staff session on the
    # challenge page instead of bouncing it off a 403.
    "accounts.middleware.TwoFactorEnforcementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                # Required by kiam_ui/base.html for the default canonical/og:url.
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                # Required by kiam-ui: its templates load the i18n tag library
                # even though this project is English-only (see below).
                "django.template.context_processors.i18n",
                # Both required by kiam-ui — see docs/design-system.md §1.
                "kiam_ui.context_processors.accessibility",
                "kiam_ui.context_processors.kiam_ui",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database — PostgreSQL + PostGIS via GeoDjango
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        **env.db("DATABASE_URL", default="postgis://postgres:postgres@localhost:5432/kiam_directory"),
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "CONN_MAX_AGE": env.int("CONN_MAX_AGE", default=60),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# GeoDjango finds libgdal/libgeos through ctypes, which searches the linker's
# default paths. Homebrew on Apple Silicon installs to /opt/homebrew/lib, which
# is not one of them, so a developer there has to point at the libraries
# explicitly. Unset everywhere else (Debian's packages land on the default path),
# and Django ignores an empty value. See README.md.
if env("GDAL_LIBRARY_PATH", default=""):
    GDAL_LIBRARY_PATH = env("GDAL_LIBRARY_PATH")
if env("GEOS_LIBRARY_PATH", default=""):
    GEOS_LIBRARY_PATH = env("GEOS_LIBRARY_PATH")

# ---------------------------------------------------------------------------
# Cache — Redis
# ---------------------------------------------------------------------------

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://localhost:6379/0"),
    }
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

AUTH_USER_MODEL = "accounts.User"

# normalize_email() lowercases only the DOMAIN, so a stored address can carry a
# capital in its local part — and ModelBackend's lookup is an exact match, which
# would reject the right password for "Nadia@example.com". See accounts/backends.py.
AUTHENTICATION_BACKENDS = ["accounts.backends.CaseInsensitiveEmailBackend"]

# Passwords are OPTIONAL and sit alongside the magic link — an account is still
# created without one (accounts.models.UserManager calls set_unusable_password),
# and only gets a password if its owner chooses to set one.
#
# The validators are Django's stock four. The minimum is 12 rather than the
# default 8: these accounts reach practitioner identity documents and a review
# queue, and 8 characters is a 2012 number.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Argon2 first. Django's default PBKDF2 is acceptable, but this database holds
# the credentials of every listed clinician and Argon2 is memory-hard — the
# difference matters only in the case where the hashes have already leaked,
# which is exactly the case worth planning for. Existing PBKDF2 hashes keep
# working and are upgraded transparently on next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

# Password sign-in attempt limits. Separate from the magic-link limits because
# the attack is different: a magic-link flood is a nuisance to one mailbox,
# whereas password attempts are credential stuffing across many accounts at
# once. The IP limit is what actually catches that, so it is the tighter of the
# two relative to normal use.
PASSWORD_LOGIN_MAX_PER_EMAIL = env.int("PASSWORD_LOGIN_MAX_PER_EMAIL", default=10)
PASSWORD_LOGIN_MAX_PER_IP = env.int("PASSWORD_LOGIN_MAX_PER_IP", default=30)
PASSWORD_LOGIN_WINDOW_SECONDS = env.int("PASSWORD_LOGIN_WINDOW_SECONDS", default=900)

# Contact-reveal limits. The reveal endpoint is POST-only and CSRF-protected, so
# the address is not in the page until somebody asks for it — this is the second
# layer, against something willing to POST. Generous rather than tight: a whole
# office behind one NAT address browsing the directory must not be locked out,
# and the thing being protected is a published business contact, not a secret.
CONTACT_REVEAL_MAX_PER_IP = env.int("CONTACT_REVEAL_MAX_PER_IP", default=60)
CONTACT_REVEAL_WINDOW_SECONDS = env.int("CONTACT_REVEAL_WINDOW_SECONDS", default=3600)

# Concern reports. Tighter, because this one writes a row someone has to read.
CONCERN_REPORT_MAX_PER_IP = env.int("CONCERN_REPORT_MAX_PER_IP", default=5)
CONCERN_REPORT_WINDOW_SECONDS = env.int("CONCERN_REPORT_WINDOW_SECONDS", default=3600)

#: Where the Django admin is mounted. Not `/admin/` — see config/urls.py.
ADMIN_URL_PATH = env("ADMIN_URL_PATH", default="staff-console")

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

MAGIC_LINK_TTL_MINUTES = env("MAGIC_LINK_TTL_MINUTES")
MAGIC_LINK_MAX_PER_EMAIL = env("MAGIC_LINK_MAX_PER_EMAIL")
MAGIC_LINK_MAX_PER_IP = env("MAGIC_LINK_MAX_PER_IP")
MAGIC_LINK_WINDOW_SECONDS = env("MAGIC_LINK_WINDOW_SECONDS")

#: Name shown in an authenticator app when a staff user enrols a TOTP device.
OTP_TOTP_ISSUER = "Kiam Clinic Directory"

# Sessions. HOST-ONLY on purpose: setting SESSION_COOKIE_DOMAIN to
# `.kiamclinic.com` would share the session across all three subdomains, which
# requires a shared user table and defeats project isolation
# (docs/multi-project-architecture.md §3 Option C, §4). Do not add it.
SESSION_COOKIE_NAME = "kiamdir_sessionid"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 12

CSRF_COOKIE_NAME = "kiamdir_csrftoken"
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True

# ---------------------------------------------------------------------------
# Internationalisation — English only, LTR only (CLAUDE.md)
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Europe/London"
USE_TZ = True
# No translation machinery in this repo. The i18n *template tags* still resolve
# with USE_I18N off — they return the single active language — which is what lets
# kiam-ui's packaged templates render unchanged. No locale dirs, no {% trans %}.
USE_I18N = False
USE_L10N = True

# ---------------------------------------------------------------------------
# Static and media
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Two storage backends that are never merged. `default` is PUBLIC (headshots);
# private verification evidence uses the `private` alias and is reachable only
# through directory.services.documents.signed_url. See docs/architecture.md
# "Storage" and the guard in directory/services/documents.py.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "private": {
        "BACKEND": "directory.storages.PrivateEvidenceStorage",
        "OPTIONS": {"location": str(BASE_DIR / "private-media"), "base_url": None},
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

#: TTL for a signed private-evidence URL. Short on purpose: the link is handed to
#: one verifier for one look, not published.
PRIVATE_DOCUMENT_URL_TTL_SECONDS = env("PRIVATE_DOCUMENT_URL_TTL_SECONDS")

# ---------------------------------------------------------------------------
# Geocoding — postcodes.io, keyless
# ---------------------------------------------------------------------------
# postcodes.io for postcode -> point, and its /places endpoint for place-name
# autocomplete, which serves OS Open Names data. That is how this project gets OS
# Open Names without an OS Data Hub key in the deployment and without a Google
# Maps bill (CLAUDE.md, "Geocoding").

GEOCODE_BASE_URL = env("GEOCODE_BASE_URL", default="https://api.postcodes.io")

# A tight timeout, on purpose. This call sits in front of a search page with a
# Core Web Vitals budget (docs/seo.md), and a slow third party must degrade to
# "searched without a location" rather than hold the page open. (connect, read).
GEOCODE_TIMEOUT_SECONDS = (
    env.float("GEOCODE_CONNECT_TIMEOUT", default=1.5),
    env.float("GEOCODE_READ_TIMEOUT", default=2.5),
)

#: A month. A postcode's coordinates do not move, and the point of caching this
#: hard is that a busy search page makes almost no outbound calls.
GEOCODE_CACHE_SECONDS = env.int("GEOCODE_CACHE_SECONDS", default=60 * 60 * 24 * 30)

#: An hour. Long enough that a typo retried five times costs one call; short
#: enough that a genuinely new postcode is not blacklisted for a month.
GEOCODE_NEGATIVE_CACHE_SECONDS = env.int("GEOCODE_NEGATIVE_CACHE_SECONDS", default=60 * 60)

#: postcodes.io asks for identification rather than a key. Being identifiable is
#: also what gets us told about a problem instead of rate-limited for one.
GEOCODE_USER_AGENT = env(
    "GEOCODE_USER_AGENT", default="KiamClinicDirectory/1.0 (+https://directory.kiamclinic.com)"
)

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

SEARCH_PAGE_SIZE = env.int("SEARCH_PAGE_SIZE", default=20)

# ---------------------------------------------------------------------------
# Submission lint
# ---------------------------------------------------------------------------

#: Prescription-only medicine blocklist, checked against practitioner free text
#: at submission (docs/content-compliance.md §1). Held OUTSIDE the code so it can
#: be updated without a deploy — the lint re-reads it when the file's mtime
#: changes. A missing file raises rather than silently permitting everything.
POM_DICTIONARY_PATH = env("POM_DICTIONARY_PATH", default=str(BASE_DIR / "ops" / "pom-dictionary.txt"))

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="Kiam Clinic Directory <no-reply@kiamclinic.com>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# kiam-ui — the shared brand chrome
# ---------------------------------------------------------------------------
# Only configuration. Colour, type and spacing belong to the package; nothing in
# this repo restates them. See docs/design-system.md for the full key reference.

KIAM_UI = {
    "SITE_NAME": "Kiam Directory",
    # Drives `site-header--directory`, so the chrome is visibly related to Kiam
    # but distinct — a hard requirement, not styling (CLAUDE.md, independence).
    "SITE_KIND": "directory",
    "HOME_URL": "/",
    "NAV_ITEMS": "pages.nav.nav_items",
    # No PRIMARY_CTA: the directory does not book anything. Kiam takes no
    # payment and manages no appointments (CLAUDE.md, "What this project is").
    "PRIMARY_CTA": None,
    # No emergency bar on the directory. kiam-ui's layer-1 marquee exists because
    # the clinic and rooms ARE service providers and have to say when they are not
    # an emergency one. This site provides no care at all — it publishes listings
    # for independent practitioners — so a scrolling "we are not an emergency
    # service" banner claims a clinical relationship the whole project exists to
    # deny, and it is a moving, attention-grabbing element on a site whose
    # audience has a high rate of anxiety and neurodevelopmental conditions
    # (golden rule #3).
    #
    # The crisis signposting required by docs/content-compliance.md §7 is a
    # SEPARATE obligation and is unaffected: it is in the footer of every page,
    # via pages.nav.footer. Turning the bar off does not touch it.
    "SHOW_DISCLAIMER_BAR": False,
    "CONTACT": {
        "street": "13 Worple Road",
        "locality": "Epsom",
        "region": "Surrey",
        "postcode": "KT18 5EP",
        "phone": "01372 660580",
        "phone_e164": "+441372660580",
        "email": "enquiries@kiamclinic.com",
    },
    "URLS": {
        "main": "https://kiamclinic.com",
        "rooms": "https://rooms.kiamclinic.com",
        "directory": "https://directory.kiamclinic.com",
    },
    # Staff and practitioners log in; clients never do (docs/architecture.md).
    "AUTH": {
        "LOGIN_URL": "accounts:login",
        "LOGOUT_URL": "accounts:logout",
        "DASHBOARD_URL": "dashboard:home",
        "DASHBOARD_LABEL": "Dashboard",
    },
    "FOOTER": "pages.nav.footer",
    # English only, LTR only. False also means the switcher is never rendered,
    # so django.conf.urls.i18n is not needed. See docs/design-system.md, Gap 1.
    "SHOW_LANGUAGE_SWITCHER": False,
}

# ---------------------------------------------------------------------------
# Logging — structured (one JSON object per line) so a log shipper can index it
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "config.logging.JSONFormatter"},
        "plain": {"format": "%(asctime)s %(levelname)-8s %(name)s %(message)s"},
    },
    "filters": {"require_debug_false": {"()": "django.utils.log.RequireDebugFalse"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        # Auth events. NEVER log a raw magic-link token — see
        # accounts/services/magic_link.py.
        "accounts": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "directory.documents": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
