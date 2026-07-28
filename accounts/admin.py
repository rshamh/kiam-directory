"""Django admin for accounts.

Superadmin only, and even then this is a repair tool rather than the way work
gets done — the staff queues in ``backoffice`` are (Phase 2).

``MagicLinkToken`` is registered read-only. A live token row is a credential; the
admin may look at one to answer "did this link get used", and may not create,
edit or hand one out.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import MagicLinkToken, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "full_name", "role", "is_active", "last_login_at")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("email", "full_name")
    ordering = ("email",)
    readonly_fields = ("date_joined", "last_login", "last_login_at")

    # No password fieldset: accounts have an unusable password by construction
    # (accounts.models.UserManager) and adding one here would create a second,
    # unaudited way in that bypasses the magic link and the TOTP requirement.
    fieldsets = (
        (None, {"fields": ("email",)}),
        ("Personal", {"fields": ("full_name",)}),
        ("Access", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Permissions", {"classes": ("collapse",), "fields": ("groups", "user_permissions")}),
        ("Dates", {"fields": ("date_joined", "last_login", "last_login_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "full_name", "role", "is_active")}),)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # DjangoUserAdmin's add form expects password fields it will not find.
        form.base_fields.pop("password", None)
        return form


@admin.register(MagicLinkToken)
class MagicLinkTokenAdmin(admin.ModelAdmin):
    """Read-only on purpose. See the module docstring."""

    list_display = ("__str__", "user", "created_at", "expires_at", "consumed_at")
    list_filter = ("created_at",)
    search_fields = ("user__email",)
    # token_hash is excluded entirely: it is not useful to a human and putting it
    # on a page invites someone to paste it somewhere.
    fields = ("user", "created_at", "expires_at", "consumed_at", "requested_ip")
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
