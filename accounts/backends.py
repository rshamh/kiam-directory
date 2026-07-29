"""Authentication backend.

Exists for one reason: ``ModelBackend`` looks the user up with
``get_by_natural_key()``, which is an **exact** match on the username field.
``BaseUserManager.normalize_email()`` lowercases only the DOMAIN, so an account
created as "Nadia@example.com" is stored with its capital N — and a sign-in
attempt for "nadia@example.com" then fails against a perfectly good password,
with the same generic error a wrong password gives.

That is a miserable failure mode: the person is certain their password is right,
and it is. ``accounts.services.magic_link`` already dodges it with an ``iexact``
lookup; this is the same fix for the password route.

The dummy-hash path is preserved deliberately. When the address is unknown,
``ModelBackend`` still runs the hasher against a throwaway user so that an
unknown address costs the same wall-clock time as a wrong password. Dropping it
would reintroduce account enumeration by timing — which is exactly what the
generic error message elsewhere is there to prevent.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class CaseInsensitiveEmailBackend(ModelBackend):
    """``ModelBackend``, but the email lookup ignores case."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()

        if username is None:
            username = kwargs.get(user_model.USERNAME_FIELD)
        if username is None or password is None:
            return None

        try:
            user = user_model._default_manager.get(email__iexact=username.strip())
        except user_model.DoesNotExist:
            # Same timing as a real attempt — see the module docstring.
            user_model().set_password(password)
            return None
        except user_model.MultipleObjectsReturned:
            # `email` is unique, so this means the constraint has been lost.
            # Fail closed rather than guess which account was meant.
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
