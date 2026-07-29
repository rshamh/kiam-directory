from django.apps import AppConfig


class DirectoryConfig(AppConfig):
    """Practitioners, taxonomy, verification, consent, audit. Empty at Phase 0 beyond the private-evidence storage helpers; the models land in Phase 1."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "directory"
    verbose_name = "Directory"

    def ready(self):
        # Imported for the side effect of registering the search-index receivers.
        # Nothing else may import this module.
        from . import signals  # noqa: F401
