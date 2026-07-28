"""Auth forms.

Plain ``forms.Form`` — none of these is backed by a model, and the login form
must deliberately not validate against one (see ``accounts.services.magic_link``
on not revealing whether an email exists).
"""

from __future__ import annotations

from django import forms


class MagicLinkRequestForm(forms.Form):
    """The only way in. There is no password field and no signup link."""

    email = forms.EmailField(
        label="Email address",
        max_length=254,
        # A visible, plain-language hint rather than a placeholder: placeholder
        # text disappears on focus and is routinely missed by people using
        # magnification or a screen reader (WCAG 3.3.2, golden rule #3).
        help_text="We'll email you a link that signs you in. No password needed.",
        widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True, "spellcheck": "false"}),
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
