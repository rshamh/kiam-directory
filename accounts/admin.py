"""Django admin for accounts.

Superadmin only, and even then this is a repair tool rather than the way work
gets done — the staff queues in ``backoffice`` are (Phase 2).

``LoginToken`` is registered read-only. A live token row is a credential; the
admin may look at one to answer "did this link get used", and may not create,
edit or hand one out.
"""

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm

from .models import Invite, LoginToken, User


class UserCreationForm(AdminUserCreationForm):
    """Django's admin creation form, with two changes this project needs.

    **The default is no password.** Django's ``usable_password`` radio defaults to
    "Enabled", which would make a settable password the normal way an account is
    created here. It is not: ``UserManager.create_user`` calls
    ``set_unusable_password()``, sign-in is by magic link, and "no password" is a
    valid permanent state (CLAUDE.md, Stack). So the radio still exists — an admin
    setting one up for somebody who asked is legitimate — but it starts at
    Disabled, and leaving it there is the path of least resistance.

    **A case-different address is a duplicate.** ``email`` is unique but
    case-SENSITIVE, and ``normalize_email()`` lowercases only the domain. Two rows
    differing only in the local part therefore satisfy the constraint while
    breaking both accounts: ``CaseInsensitiveEmailBackend`` catches
    ``MultipleObjectsReturned`` and fails closed for BOTH, and
    ``magic_link.for_email()`` uses ``.first()``, so a link would be sent to
    whichever row the database returned. The admin add form is exactly where a
    human types "Nadia@Example.com", so the check belongs here.
    """

    usable_password = forms.ChoiceField(
        label="Password-based authentication",
        required=False,
        initial="false",
        choices={"true": "Enabled", "false": "Disabled"},
        widget=forms.RadioSelect(attrs={"class": "radiolist inline"}),
        help_text=(
            "Leave disabled unless this person has asked for a password. Accounts "
            "sign in by magic link; a password is optional and its owner can set "
            "their own from Account &amp; security."
        ),
    )

    class Meta(AdminUserCreationForm.Meta):
        model = User
        fields = ("email",)
        # Django's own Meta maps "username" to UsernameField. There is no username
        # on this model, so the mapping is dead weight — and it would be applied to
        # a field of that name if one were ever added.
        field_classes = {}

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "An account already uses this address. Addresses are matched "
                "without regard to case, because sign-in is."
            )
        return email


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

    add_form = UserCreationForm

    # `password` is Django's ReadOnlyPasswordHashField: it renders the algorithm
    # and a "Set password" button linking to ../password/, never anything that
    # could be replayed. It was omitted here from Phase 0 on the reasoning that a
    # settable password would be "a second, unaudited way in" — which was true
    # while magic link was the only route. It no longer is: optional password
    # sign-in has been live since Phase 1, so leaving the field out did not
    # prevent password authentication, it only prevented an admin helping
    # somebody locked out of it.
    #
    # It is not a bypass of anything. Django's own change-password view logs a
    # LogEntry naming the admin who did it, the admin itself is superadmin-only
    # and behind TwoFactorEnforcementMiddleware, and setting a password does not
    # satisfy a TOTP challenge.
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal", {"fields": ("display_name",)}),
        ("Access", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Security", {"fields": ("totp_enabled", "email_verified_at")}),
        ("Permissions", {"classes": ("collapse",), "fields": ("groups", "user_permissions")}),
        ("Dates", {"fields": ("date_joined", "last_login", "last_login_at")}),
    )
    # `usable_password`, `password1` and `password2` are DECLARED fields on the
    # creation form, so they are required whether or not they are rendered.
    # Leaving them out of this tuple did not remove them — it hid them, and every
    # attempt to add a user died on "This field is required" for two inputs that
    # were not on the page. No account could be created through the admin at all
    # between Phase 0 and this fix.
    #
    # test_every_required_field_on_the_add_form_is_rendered is the general guard.
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "display_name", "role", "is_active")}),
        (
            "Password",
            {
                "classes": ("wide",),
                "fields": ("usable_password", "password1", "password2"),
                "description": (
                    "Accounts normally have no password and sign in by magic link. "
                    "Leave this disabled unless the person has asked for one."
                ),
            },
        ),
    )


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
