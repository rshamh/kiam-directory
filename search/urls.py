"""Search URLs.

``/search/`` is one URL that answers two ways — the whole page on a normal GET,
the results fragment when HTMX asks. There is deliberately no separate
``/search/results/``: a fragment reachable at its own address is a second
indexable surface serving the same listings with none of the chrome, which is
exactly what ``docs/seo.md`` says not to build.

Faceted URLs are query strings on this one path, so the canonical and the
``noindex, follow`` in ``search.views._seo`` cover every permutation.
"""

from django.urls import path

from . import views

app_name = "search"

urlpatterns = [
    path("search/", views.search, name="search"),
    # Feeds the location field's <datalist>. Head-less HTML, so it cannot carry a
    # noindex META tag — it is protected by the X-Robots-Tag HEADER the view sets
    # and by a Disallow in robots.txt. See the view's docstring: the earlier
    # reasoning here ("no links to follow") tested the wrong thing.
    path("search/places/", views.place_suggestions, name="place_suggestions"),
]
