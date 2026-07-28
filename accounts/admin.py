"""Django admin for accounts.

Superadmin only, and even then this is a repair tool rather than the way work
gets done — the staff queues in ``backoffice`` are (Phase 2).

``LoginToken`` is registered read-only. A live token row is a credential; the
admin may look at one to answer "did this link get used", and may not create,
edit or hand one out.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Invite, LoginToken, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "display_name", "role", "is_active", "totp_enabled", "last_login_at")
    list_filter = ("role", "is_active", "is_staff", "totp_enabled")
    search_fields = ("email", "display_name")
    ordering = ("email",)
    # totp_enabled is COMPUTED from whether a confirmed device exists
    # (accounts.services.two_factor._sync_flag). Editable here it would be a
    # boolean anyone could tick to claim a second factor that does not exist.
    readonly_fields = ("date_joined", "last_login", "last_login_at", "email_verified_at", "totp_enabled")

    # No password fieldset. Accounts are created with an unusable password
    # (accounts.models.UserManager.create_user) and sign in by magic link; adding
    # a settable password here would create a second, unaudited way in.
    #
    # createsuperuser is the one exception and sets one deliberately, because
    # Django's own admin login form needs it. That is still not a bypass: the
    # admin sits behind TwoFactorEnforcementMiddleware, so a password alone
    # reaches the TOTP challenge and stops there.
    fieldsets = (
        (None, {"fields": ("email",)}),
        ("Personal", {"fields": ("display_name",)}),
        ("Access", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Security", {"fields": ("totp_enabled", "email_verified_at")}),
        ("Permissions", {"classes": ("collapse",), "fields": ("groups", "user_permissions")}),
        ("Dates", {"fields": ("date_joined", "last_login", "last_login_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "display_name", "role", "is_active")}),
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # DjangoUserAdmin's add form expects password fields it will not find.
        form.base_fields.pop("password", None)
        return form


@admin.register(LoginToken)
class LoginTokenAdmin(admin.ModelAdmin):
    """Read-only on purpose. See the module docstring."""

    list_display = ("user", "created_at", "expires_at", "used_at")
    list_filter = ("created_at",)
    search_fields = ("user__email",)
    # token_hash is excluded entirely: it is not useful to a human and putting it
    # on a page invites someone to paste it somewhere.
    fields = ("user", "created_at", "expires_at", "used_at", "requested_ip")
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    """Read-only until Phase 2 builds the issue/accept flow.

    The model is authored and lands with accounts/models.py, but nothing issues
    an Invite yet. Leaving it addable here would let someone create a row with a
    token_hash nobody holds the token for — a dead invitation that looks live.
    """

    list_display = ("email", "invited_by", "created_at", "expires_at", "accepted_at")
    search_fields = ("email",)
    fields = ("email", "invited_by", "created_at", "expires_at", "accepted_at", "note")
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
