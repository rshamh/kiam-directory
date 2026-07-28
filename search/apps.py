from django.apps import AppConfig


class SearchConfig(AppConfig):
    """Query parsing, geocoding, the search view and its HTMX partials. Empty until Phase 4."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "search"
    verbose_name = "Search"
