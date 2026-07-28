"""Issuing and consuming magic links.

The whole flow, and the reasoning for each control:

    request  ->  rate-limit (email + IP)
             ->  find the user, or don't
             ->  mint 32 random bytes, store ONLY the SHA-256 hash
             ->  email the raw token
             ->  ALWAYS return the same response

    consume  ->  hash the presented token
             ->  constant-time compare against the stored hash
             ->  reject if consumed or expired
             ->  mark consumed, invalidate the user's other live tokens
             ->  cycle the session key, then log in

Three rules that are easy to lose in a refactor:

**Never log or store the raw token.** It is generated, put in one email, and
dropped. Log lines carry the user id and the token's primary key, never the
secret. A logging call that included it would put a working credential into every
log aggregator the project ever ships to.

**Never reveal whether an email exists.** ``request_link`` returns the same value
whether the address matched an account, matched a deactivated account, or matched
nothing. The directory lists named clinicians whose addresses are semi-public;
an oracle here tells an attacker exactly which of them hold accounts.

**Hash before comparing, and compare in constant time.** The hash lookup is a
plain index hit, so the comparison is not what an attacker can time — but a
future change that swaps the lookup for a scan would silently reintroduce the
leak, and ``compare_digest`` costs nothing.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from ..models import MagicLinkToken
from . import ratelimit

logger = logging.getLogger("accounts")

User = get_user_model()

#: Rate-limit scopes. Separate so an attacker spraying one address cannot
#: exhaust an unrelated visitor's IP budget, and vice versa.
SCOPE_EMAIL = "magiclink:email"
SCOPE_IP = "magiclink:ip"


class RateLimited(Exception):
    """Too many magic-link requests for this email or this address."""


def hash_token(raw_token: str) -> str:
    """SHA-256 of the raw token.

    Plain SHA-256, not a password hash: the token is 32 bytes of CSPRNG output,
    so there is no low-entropy secret to slow a guesser down over. Argon2 here
    would buy nothing and would put a deliberately expensive function on a path
    an anonymous caller can trigger.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _check_rate_limits(email: str, ip: str | None) -> None:
    window = settings.MAGIC_LINK_WINDOW_SECONDS

    by_email = ratelimit.hit(
        SCOPE_EMAIL, email, limit=settings.MAGIC_LINK_MAX_PER_EMAIL, window_seconds=window
    )
    if by_email.exceeded:
        logger.warning("magic_link.rate_limited", extra={"scope": "email"})
        raise RateLimited

    if ip:
        by_ip = ratelimit.hit(SCOPE_IP, ip, limit=settings.MAGIC_LINK_MAX_PER_IP, window_seconds=window)
        if by_ip.exceeded:
            logger.warning("magic_link.rate_limited", extra={"scope": "ip", "ip": ip})
            raise RateLimited


def issue(user, *, ip: str | None = None, user_agent: str = "") -> tuple[MagicLinkToken, str]:
    """Mint a token for ``user``. Returns the row and the RAW token.

    The raw value is returned rather than stored so the caller can put it in the
    email; nothing else may keep it.
    """
    raw_token = MagicLinkToken.new_raw_token()
    token = MagicLinkToken.objects.create(
        user=user,
        token_hash=hash_token(raw_token),
        expires_at=MagicLinkToken.default_expiry(),
        requested_ip=ip,
        requested_user_agent=user_agent[:300],
    )
    logger.info("magic_link.issued", extra={"user_id": user.pk, "token_id": token.pk})
    return token, raw_token


def build_link(raw_token: str) -> str:
    path = reverse("accounts:magic_link_consume", kwargs={"token": raw_token})
    return urljoin(settings.SITE_BASE_URL + "/", path.lstrip("/"))


def send_link_email(user, raw_token: str) -> None:
    from django.core.mail import EmailMultiAlternatives

    context = {
        "link": build_link(raw_token),
        "ttl_minutes": settings.MAGIC_LINK_TTL_MINUTES,
        "site_name": settings.KIAM_UI["SITE_NAME"],
    }
    subject = f"Your sign-in link for {context['site_name']}"
    body = render_to_string("accounts/email/magic_link.txt", context)

    message = EmailMultiAlternatives(
        subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=[user.email]
    )
    message.send(fail_silently=False)


def request_link(email: str, *, ip: str | None = None, user_agent: str = "") -> None:
    """Handle a login request.

    Returns ``None`` in every case that is not rate limiting — a caller cannot
    tell whether an email was sent, because the return value carries no signal.
    The only exception raised is ``RateLimited``, which is about the *requester*
    and reveals nothing about the account.
    """
    email = email.strip().lower()
    _check_rate_limits(email, ip)

    user = User.objects.filter(email=email, is_active=True).first()
    if user is None:
        # No email, no token, no timing shortcut worth engineering around: the
        # response is identical and the work skipped here is a hash and an SMTP
        # call, neither of which is observable through the rendered page.
        logger.info("magic_link.requested_unknown_email")
        return

    _, raw_token = issue(user, ip=ip, user_agent=user_agent)
    send_link_email(user, raw_token)


@transaction.atomic
def consume(raw_token: str) -> User | None:
    """Redeem a token. Returns the user, or ``None`` if it is not usable."""
    if not raw_token:
        return None

    presented_hash = hash_token(raw_token)

    token = (
        MagicLinkToken.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=presented_hash)
        .first()
    )
    if token is None:
        logger.info("magic_link.consume_rejected", extra={"reason": "not_found"})
        return None

    # Belt and braces — see the module docstring.
    if not hmac.compare_digest(token.token_hash, presented_hash):
        logger.info("magic_link.consume_rejected", extra={"reason": "mismatch"})
        return None

    if token.is_consumed:
        logger.warning(
            "magic_link.consume_rejected",
            extra={"reason": "already_consumed", "token_id": token.pk, "user_id": token.user_id},
        )
        return None

    if token.is_expired:
        logger.info(
            "magic_link.consume_rejected",
            extra={"reason": "expired", "token_id": token.pk, "user_id": token.user_id},
        )
        return None

    if not token.user.is_active:
        logger.warning(
            "magic_link.consume_rejected",
            extra={"reason": "inactive_user", "user_id": token.user_id},
        )
        return None

    now = timezone.now()
    token.consumed_at = now
    token.save(update_fields=["consumed_at"])

    # One live link per person. Redeeming the newest link invalidates any older
    # one still sitting in an inbox — otherwise a forwarded or leaked earlier
    # email stays valid for the rest of its TTL.
    MagicLinkToken.objects.filter(user=token.user, consumed_at__isnull=True).exclude(pk=token.pk).update(
        consumed_at=now
    )

    logger.info("magic_link.consumed", extra={"user_id": token.user_id, "token_id": token.pk})
    return token.user


def log_in(request, user) -> None:
    """Establish the session.

    ``login()`` cycles the session key itself, which is what closes session
    fixation: a key an attacker planted before authentication does not survive it.
    """
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    user.last_login_at = timezone.now()
    user.save(update_fields=["last_login_at"])


def purge_expired(*, older_than_days: int = 7) -> int:
    """Delete spent and expired tokens. Wired to a scheduler in Phase 2.

    Consumed rows are kept briefly so "this link was already used" can be
    answered honestly, then dropped — GDPR minimisation, and there is no reason
    to retain a record of every login attempt indefinitely.
    """
    from datetime import timedelta

    cutoff = timezone.now() - timedelta(days=older_than_days)
    deleted, _ = MagicLinkToken.objects.filter(created_at__lt=cutoff).delete()
    return deleted
