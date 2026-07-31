from django.apps import AppConfig


class BackofficeConfig(AppConfig):
    """Admin queues: invites, review, verification workbench, suspensions. Empty until Phase 2."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.backoffice"
    verbose_name = "Back office"
