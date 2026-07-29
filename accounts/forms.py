"""Auth forms.

Plain ``forms.Form`` — none of these is backed by a model, and the login form
must deliberately not validate against one (see ``accounts.services.magic_link``
on not revealing whether an email exists).
"""

from __future__ import annotations

from django import forms


class MagicLinkRequestForm(forms.Form):
    """Sign in — by password, or by emailing a link.

    ONE form and one submit button rather than two tabs. The password field is
    optional: fill it in and it is a password sign-in, leave it blank and we
    email a link. That means somebody who has never set a password does not have
    to know which of two flows applies to them, and somebody who has set one does
    not have to hunt for the right tab.

    There is still no signup link — account creation is by admin invite only.
    """

    email = forms.EmailField(
        label="Email address",
        max_length=254,
        # A visible, plain-language hint rather than a placeholder: placeholder
        # text disappears on focus and is routinely missed by people using
        # magnification or a screen reader (WCAG 3.3.2, golden rule #3).
        help_text="We'll email you a link that signs you in. No password needed.",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True, "spellcheck": "false"}),
    )

    password = forms.CharField(
        label="Password",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
        help_text="Leave blank if you'd rather we emailed you a sign-in link.",
    )

    # A honeypot, not a CAPTCHA. This audience has a high rate of
    # neurodevelopmental and anxiety conditions and a puzzle at the door is a real
    # barrier; a field no human sees costs them nothing (golden rules #3 and #4).
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}),
        label="Leave this field empty",
    )

    def clean_email(self) -> str:
        return self.cleaned_data["email"].strip().lower()

    @property
    def wants_password_login(self) -> bool:
        return bool((self.cleaned_data.get("password") or "").strip())

    @property
    def is_bot(self) -> bool:
        """True when the honeypot was filled in."""
        return bool(self.cleaned_data.get("website"))


class TOTPCodeForm(forms.Form):
    """A six-digit code from an authenticator app."""

    code = forms.CharField(
        label="Six-digit code",
        min_length=6,
        max_length=6,
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "autofocus": True,
            }
        ),
    )

    def clean_code(self) -> str:
        code = self.cleaned_data["code"].replace(" ", "")
        if not code.isdigit():
            raise forms.ValidationError("Enter the six digits shown in your authenticator app.")
        return code


class SetPasswordForm(forms.Form):
    """Set or change a password.

    Validated against ``AUTH_PASSWORD_VALIDATORS``, and the errors are shown as
    they come — Django's messages are already plain English, and rewriting them
    tends to lose the specific reason a password was rejected.

    Confirmation field because there is no "show password" toggle and a typo in
    a password you cannot see locks you out of the thing you just secured. The
    magic link would still get them back in, but discovering that costs a
    support email.
    """

    new_password = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password", "autofocus": True}),
        help_text="At least 12 characters. A short phrase you'll remember beats a short jumble.",
    )
    confirm_password = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        # The user is needed for UserAttributeSimilarityValidator, which is what
        # stops somebody using their own email address as their password.
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_new_password(self) -> str:
        from django.contrib.auth.password_validation import validate_password

        password = self.cleaned_data["new_password"]
        validate_password(password, user=self.user)
        return password

    def clean(self):
        data = super().clean()
        new = data.get("new_password")
        confirm = data.get("confirm_password")

        if new and confirm and new != confirm:
            self.add_error("confirm_password", "These two don't match.")
        return data
