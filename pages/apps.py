from django.apps import AppConfig


class PagesConfig(AppConfig):
    """Static content pages, the chrome configuration callables, and the curated landing pages from Phase 7."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "pages"
    verbose_name = "Pages"
