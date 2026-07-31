"""Root URL configuration.

Two things to notice:

* **No signup route**, anywhere. Account creation is by admin ``Invite`` only
  (Phase 2). ``accounts/tests/test_no_signup.py`` asserts it stays that way.
* **The Django admin is not at ``/admin/``.** It is behind ``ADMIN_URL_PATH`` so
  it is not on the first page of anyone's scanner wordlist. That is obscurity,
  not security — the real control is ``accounts.access.can_use_django_admin``
  plus enforced TOTP — but it removes the constant background noise.
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from apps.seo.sitemaps import SITEMAPS

ADMIN_PATH = settings.ADMIN_URL_PATH.strip("/")

urlpatterns = [
    # robots.txt, llms.txt and /healthz live at the root.
    path("", include("apps.seo.urls")),
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("accounts/", include("apps.accounts.urls")),
    # Public practitioner profiles at /p/<slug>/.
    path("", include("apps.directory.urls")),
    # /search/ and its place-suggestion partial.
    path("", include("apps.search.urls")),
    # A practitioner's own listing. `can_use_dashboard` + `owns_practitioner` on
    # every view, noindex, and Disallow-ed in robots.txt — it is somebody's
    # personal data behind a login, and a robots directive should not be the only
    # thing keeping it out of an index.
    path("dashboard/", include("apps.dashboard.urls")),
    # Staff only. Every view carries an accounts.access predicate and the 2FA
    # middleware gates the whole prefix; also Disallow-ed in robots.txt.
    path("backoffice/", include("apps.backoffice.urls")),
    path(f"{ADMIN_PATH}/", admin.site.urls),
    # Last: `pages` owns the bare root path.
    path("", include("apps.pages.urls")),
]

handler404 = "apps.seo.views.not_found"
handler500 = "apps.seo.views.server_error"

if settings.DEBUG:
    # Serve uploaded media in development only.
    #
    # Without this every headshot 404s locally — on search results and on the
    # profile page — because `runserver` serves `STATIC_URL` and nothing else. That
    # went unnoticed from Phase 3 until a development database had headshots in it
    # to look at, and it made the one image on the site invisible to anyone
    # developing against it.
    #
    # DEBUG-only, deliberately: in production headshots come from the public S3
    # bucket (`config/settings/prod.py`) via a CDN, and `django.views.static.serve`
    # is single-threaded, does no caching and is explicitly not for production.
    # Private verification evidence is NOT affected either way — it lives on a
    # separate backend with no public URL at all, reachable only through
    # `directory.services.documents.open_evidence()`.
    from django.conf.urls.static import static

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    try:
        import debug_toolbar  # noqa: F401
    except ImportError:
        pass
    else:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
