"""Password sign-in, sitting alongside the magic link.

Passwords are **optional**. An account is created without one
(``accounts.models.UserManager`` calls ``set_unusable_password``) and only gets
one if its owner chooses to set it. Both routes stay open: someone who never
sets a password keeps signing in exactly as before.

Three properties carried over from the magic-link flow, because weakening them
here would weaken them everywhere:

**No user enumeration.** A wrong password and an address with no account produce
the same message and the same timing. Django's ``ModelBackend`` runs the hasher
against a dummy user when the address is unknown, so the timing side is handled
for us — but only if we go through ``authenticate()`` rather than looking the
user up first and short-circuiting. This module does not look the user up first.

**No password means no password login.** An account with an unusable password
cannot be signed into with one, and — this is the part that is easy to get wrong
— it must fail the same way a wrong password does, not with "this account has no
password", which would tell an attacker exactly which accounts to target with a
reset.

**Rate limited per email AND per IP.** The IP limit is the one that matters:
password attempts are credential stuffing across many accounts, which a
per-email limit alone does not see at all.

Recovery is deliberately NOT a separate token flow. "Forgot your password" sends
a magic link, which signs them in and lets them set a new one — one
credential-recovery path with one set of rate limits and one expiry, instead of
two that can drift apart.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import authenticate, login, update_session_auth_hash
from django.utils import timezone

from . import ratelimit

logger = logging.getLogger("accounts")

SCOPE_EMAIL = "password:email"
SCOPE_IP = "password:ip"


class RateLimited(Exception):
    """Too many password attempts for this email or this address."""


def check_rate_limits(email: str, ip: str | None) -> None:
    """Count this attempt. Raises ``RateLimited`` when either budget is spent."""
    window = settings.PASSWORD_LOGIN_WINDOW_SECONDS

    by_email = ratelimit.hit(
        SCOPE_EMAIL,
        email.strip().lower(),
        limit=settings.PASSWORD_LOGIN_MAX_PER_EMAIL,
        window_seconds=window,
    )
    if by_email.exceeded:
        logger.warning("password.rate_limited", extra={"scope": "email"})
        raise RateLimited

    if ip:
        by_ip = ratelimit.hit(SCOPE_IP, ip, limit=settings.PASSWORD_LOGIN_MAX_PER_IP, window_seconds=window)
        if by_ip.exceeded:
            logger.warning("password.rate_limited", extra={"scope": "ip", "ip": ip})
            raise RateLimited


def attempt(request, email: str, raw_password: str, *, ip: str | None = None):
    """Try to sign in with a password. Returns the user, or ``None``.

    ``None`` covers every failure — wrong password, no such account, account
    deactivated, account has no password set. The caller shows one message for
    all of them.

    Note this goes straight to ``authenticate()`` rather than fetching the user
    first: the backend's own dummy-hash path is what keeps an unknown address
    taking the same time as a wrong password.
    """
    check_rate_limits(email, ip)

    user = authenticate(request, username=email.strip().lower(), password=raw_password)

    if user is None:
        logger.info("password.attempt_failed")
        return None

    # ModelBackend already refuses inactive users via user_can_authenticate,
    # but assert it here too rather than depending on a backend setting that
    # somebody could change without realising what it gates.
    if not user.is_active:
        logger.warning("password.attempt_inactive", extra={"user_id": user.pk})
        return None

    logger.info("password.attempt_succeeded", extra={"user_id": user.pk})
    return user


def log_in(request, user) -> None:
    """Establish the session after a successful password attempt.

    Mirrors ``magic_link.log_in`` — ``login()`` cycles the session key, which is
    what closes session fixation. It does NOT set ``email_verified_at``: a
    password proves knowledge of a secret, not control of the mailbox, and only
    redeeming a magic link demonstrates the latter.
    """
    login(request, user, backend="accounts.backends.CaseInsensitiveEmailBackend")

    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])

    # Spend the rate-limit budget only on failures, so a person who signs in
    # correctly ten times in a morning is not locked out.
    ratelimit.reset(SCOPE_EMAIL, user.email)


def set_password(user, raw_password: str, *, request=None) -> None:
    """Set or replace a password.

    ``update_session_auth_hash`` keeps the current session alive: without it
    Django invalidates every session for the user on a password change,
    including the one they are using, and they would be logged straight out by
    the act of setting a password.

    Every OTHER session is still invalidated, which is the desired half of that
    behaviour — changing a password after a suspected compromise should end the
    attacker's session.
    """
    user.set_password(raw_password)
    user.save(update_fields=["password"])

    if request is not None and request.user.is_authenticated and request.user.pk == user.pk:
        update_session_auth_hash(request, user)

    logger.info("password.set", extra={"user_id": user.pk})


def clear_password(user) -> None:
    """Remove the password, returning the account to magic-link only.

    Offered because the magic link never went away: someone who set a password
    and would rather not have one should be able to undo it, not be stuck with a
    credential they did not want.
    """
    user.set_unusable_password()
    user.save(update_fields=["password"])
    logger.info("password.cleared", extra={"user_id": user.pk})


def has_password(user) -> bool:
    return bool(user and user.is_authenticated and user.has_usable_password())
