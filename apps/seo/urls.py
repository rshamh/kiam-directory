"""SEO and ops URLs.

Mounted at the site root by ``config/urls.py`` — ``robots.txt`` and ``llms.txt``
are only honoured at the root of the host they apply to.
"""

from django.urls import path

from . import views

app_name = "seo"

urlpatterns = [
    path("robots.txt", views.robots_txt, name="robots"),
    path("llms.txt", views.llms_txt, name="llms"),
    path("healthz", views.healthz, name="healthz"),
]
