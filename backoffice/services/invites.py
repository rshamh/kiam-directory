"""Invitations — the only way an account comes into existence.

There is no public signup route and none may be added until the
public-registration phase, which needs stricter identity checks than an email
round-trip (``accounts/models.py``). Every practitioner account starts here, with
a named admin behind it and an audit entry.

The token handling mirrors ``accounts.services.magic_link`` deliberately: 32
CSPRNG bytes, only the SHA-256 hash stored, single use, expiring. An invite is a
credential — anyone holding it gets an account on a directory of verified
clinicians — so it is treated like one.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta
from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.models import Invite, User
from directory.models import AuditLog

logger = logging.getLogger("backoffice.invites")

TOKEN_BYTES = 32
DEFAULT_TTL_DAYS = 14


class InviteError(Exception):
    """The invite cannot be issued or accepted."""


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@transaction.atomic
def issue(email: str, *, invited_by, note: str = "", ttl_days: int = DEFAULT_TTL_DAYS) -> tuple[Invite, str]:
    """Create an invite. Returns the row and the RAW token.

    The raw token is returned so the caller can email it, and is stored nowhere.
    """
    email = email.strip().lower()
    if not email:
        raise InviteError("An invite needs an email address.")

    if User.objects.filter(email__iexact=email).exists():
        # Not an oracle: this is the staff-facing side, behind role + TOTP.
        raise InviteError(f"{email} already has an account.")

    # Supersede any earlier unaccepted invite rather than leaving two live
    # tokens for one address — the older email becomes a dead link, which is
    # what someone re-inviting expects to happen.
    superseded = Invite.objects.filter(email__iexact=email, accepted_at__isnull=True).delete()[0]

    raw_token = secrets.token_urlsafe(TOKEN_BYTES)
    invite = Invite.objects.create(
        email=email,
        token_hash=hash_token(raw_token),
        invited_by=invited_by,
        expires_at=timezone.now() + timedelta(days=ttl_days),
        note=note,
    )

    AuditLog.objects.create(
        actor=invited_by,
        action="invite.issued",
        entity_type="Invite",
        entity_id=str(invite.pk),
        after={"email": email, "expires_at": invite.expires_at.isoformat(), "superseded": superseded},
    )
    logger.info(
        "invite.issued",
        extra={"invite_id": invite.pk, "actor_id": getattr(invited_by, "pk", None)},
    )
    return invite, raw_token


def build_link(raw_token: str) -> str:
    path = reverse("backoffice:invite_accept", kwargs={"token": raw_token})
    return urljoin(settings.SITE_BASE_URL + "/", path.lstrip("/"))


def send(invite: Invite, raw_token: str) -> None:
    body = render_to_string(
        "backoffice/email/invite.txt",
        {
            "link": build_link(raw_token),
            "expires_at": invite.expires_at,
            "site_name": settings.KIAM_UI["SITE_NAME"],
            "note": invite.note,
        },
    )
    send_mail(
        subject=f"You've been invited to list on the {settings.KIAM_UI['SITE_NAME']}",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[invite.email],
        fail_silently=False,
    )


def issue_and_send(email: str, *, invited_by, note: str = "") -> Invite:
    invite, raw_token = issue(email, invited_by=invited_by, note=note)
    send(invite, raw_token)
    return invite


def lookup(raw_token: str) -> Invite | None:
    """The invite for this token, if it is usable. None otherwise.

    Expired, already-accepted and unknown all return None — the accept view
    shows one page for all three, so a stale link tells the holder nothing about
    whether it was ever real.
    """
    if not raw_token:
        return None

    presented = hash_token(raw_token)
    invite = Invite.objects.filter(token_hash=presented).first()
    if invite is None:
        return None
    if not hmac.compare_digest(invite.token_hash, presented):
        return None
    if invite.accepted_at is not None or invite.expires_at <= timezone.now():
        return None
    return invite


@transaction.atomic
def accept(raw_token: str) -> User | None:
    """Redeem an invite into a practitioner account and an empty draft profile.

    Returns the new user, or None if the token is not usable.

    The account is created with an unusable password, like every other account
    here — they sign in by magic link from this point on.
    """
    from directory.models import Practitioner

    invite = lookup(raw_token)
    if invite is None:
        logger.info("invite.accept_rejected")
        return None

    invite = Invite.objects.select_for_update().get(pk=invite.pk)
    if invite.accepted_at is not None:
        return None

    user = User.objects.create_user(email=invite.email, role=User.Role.PRACTITIONER)

    # The empty draft they land on. Created here rather than on first dashboard
    # visit so that "invited" and "has somewhere to type" are the same event.
    practitioner = Practitioner.objects.create(
        user=user,
        slug=_unique_slug(invite.email),
        full_name="",
        status=Practitioner._meta.get_field("status").default,
    )

    invite.accepted_at = timezone.now()
    invite.save(update_fields=["accepted_at"])

    AuditLog.objects.create(
        actor=user,
        action="invite.accepted",
        entity_type="Practitioner",
        entity_id=str(practitioner.pk),
        after={"invite_id": invite.pk, "email": invite.email},
    )
    logger.info("invite.accepted", extra={"invite_id": invite.pk, "user_id": user.pk})
    return user


def _unique_slug(email: str) -> str:
    """A placeholder slug the practitioner replaces when they fill the profile in.

    Derived from the local part plus random suffix rather than left blank,
    because `slug` is unique and NOT NULL and two invites accepted in the same
    second would otherwise collide.
    """
    from django.utils.text import slugify

    base = slugify(email.split("@")[0]) or "practitioner"
    return f"{base}-{secrets.token_hex(4)}"
