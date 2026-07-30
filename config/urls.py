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

from seo.sitemaps import SITEMAPS

ADMIN_PATH = settings.ADMIN_URL_PATH.strip("/")

urlpatterns = [
    # robots.txt, llms.txt and /healthz live at the root.
    path("", include("seo.urls")),
    path(
        "sitemap.xml",
        sitemap,
        {"sitemaps": SITEMAPS},
        name="django.contrib.sitemaps.views.sitemap",
    ),
    path("accounts/", include("accounts.urls")),
    # Public practitioner profiles at /p/<slug>/.
    path("", include("directory.urls")),
    # /search/ and its place-suggestion partial.
    path("", include("search.urls")),
    # Staff only. Every view carries an accounts.access predicate and the 2FA
    # middleware gates the whole prefix; also Disallow-ed in robots.txt.
    path("backoffice/", include("backoffice.urls")),
    path(f"{ADMIN_PATH}/", admin.site.urls),
    # Last: `pages` owns the bare root path.
    path("", include("pages.urls")),
]

handler404 = "seo.views.not_found"
handler500 = "seo.views.server_error"

if settings.DEBUG:
    try:
        import debug_toolbar  # noqa: F401
    except ImportError:
        pass
    else:
        urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
