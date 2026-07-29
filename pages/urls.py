"""Static and content page URLs.

The static set is generated from ``pages.content.STATIC_PAGES`` so a page's URL,
name, title and meta description are declared in one place. Adding a row there
adds the URL, the view wiring and the sitemap entry together.

``pages`` is included LAST in ``config/urls.py`` because it owns the bare root
path, so nothing here may use a prefix another app might want.
"""

from django.urls import path

from . import content, views

app_name = "pages"

urlpatterns = [
    path("", views.home, name="home"),
    path("report-a-concern/", views.report_concern, name="report_concern"),
    *[
        path(page.path, views.static_page, {"page_name": page.name}, name=page.name)
        for page in content.STATIC_PAGES
    ],
]
