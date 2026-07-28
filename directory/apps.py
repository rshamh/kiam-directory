from django.apps import AppConfig


class DirectoryConfig(AppConfig):
    """Practitioners, taxonomy, verification, consent, audit. Empty at Phase 0 beyond the private-evidence storage helpers; the models land in Phase 1."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "directory"
    verbose_name = "Directory"
