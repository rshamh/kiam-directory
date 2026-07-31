from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Thin User, magic-link authentication, and every role predicate in access.py."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    verbose_name = "Accounts"
