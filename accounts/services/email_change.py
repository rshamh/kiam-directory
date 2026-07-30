"""Changing the address an account signs in with.

Email is the identifier here — there are no usernames — so this changes the
credential, not a contact detail. That is why it needs both halves:

* **Confirm at the CURRENT address** so somebody who finds an unattended session
  cannot move the account somewhere its owner cannot follow. Without this, a
  logged-in stranger changes the address, then requests a magic link, and the
  account is theirs.
* **Confirm at the NEW address** so a typo does not lock the owner out
  permanently. Without this, "nadia@exmaple.com" is an account nobody can ever
  sign in to again.

Neither alone is enough, and the failure modes are opposite, which is why the
brief asks for both.

Token handling is ``accounts.services.magic_link``'s, deliberately: CSPRNG bytes,
only the SHA-256 hash stored, single use, and the whole request expires. Two
independent tokens, so confirming at one address tells you nothing about the other.

**Nothing changes until both are in.** ``apply()`` is called by whichever
confirmation lands second, inside one transaction, and re-checks that the address
is still free — an hour can pass between the two clicks and somebody else may have
been invited onto it.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from directory.models import AuditLog

from ..models import EmailChangeRequest, User

logger = logging.getLogger("accounts.email_change")

TOKEN_BYTES = 32


def ttl_hours() -> int:
    return getattr(settings, "EMAIL_CHANGE_TTL_HOURS", 24)


class EmailChangeError(Exception):
    """The change cannot be started or applied. Message is shown to the user."""


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def open_request_for(user) -> EmailChangeRequest | None:
    """The user's pending change, if any is still live."""
    return next(
        (r for r in user.email_changes.order_by("-created_at")[:5] if r.is_open),
        None,
    )


@transaction.atomic
def start(user, new_email: str, *, ip: str | None = None) -> EmailChangeRequest:
    """Begin a change. Emails both addresses. Returns the request.

    Supersedes any earlier open request rather than leaving two live: two pending
    changes for one account means whichever pair of links is clicked first wins,
    which is not something anybody can reason about.
    """
    new_email = (new_email or "").strip().lower()

    if not new_email:
        raise EmailChangeError("Enter the new email address.")
    if new_email == user.email.lower():
        raise EmailChangeError("That is already your email address.")
    if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
        # Not an enumeration oracle worth worrying about: the person asking is
        # already signed in as somebody, and the alternative — accepting it and
        # failing at `apply()` after two confirmation clicks — is worse.
        raise EmailChangeError("There is already an account using that address.")

    user.email_changes.filter(completed_at__isnull=True, cancelled_at__isnull=True).update(
        cancelled_at=timezone.now()
    )

    current_raw = secrets.token_urlsafe(TOKEN_BYTES)
    new_raw = secrets.token_urlsafe(TOKEN_BYTES)

    request = EmailChangeRequest.objects.create(
        user=user,
        new_email=new_email,
        current_token_hash=hash_token(current_raw),
        new_token_hash=hash_token(new_raw),
        expires_at=timezone.now() + timedelta(hours=ttl_hours()),
        requested_ip=ip,
    )

    _send(user.email, user=user, request=request, raw_token=current_raw, which="current")
    _send(new_email, user=user, request=request, raw_token=new_raw, which="new")

    AuditLog.objects.create(
        actor=user,
        action="account.email_change_started",
        entity_type="User",
        entity_id=str(user.pk),
        # The NEW address is recorded; the old one is already on the row this
        # points at. Tokens are not, obviously.
        after={"new_email": new_email, "expires_at": request.expires_at.isoformat()},
    )
    logger.info("email_change.started", extra={"user_id": user.pk})
    return request


def peek(raw_token: str) -> EmailChangeRequest | None:
    """The request a token belongs to, **without recording anything**.

    What the confirmation page renders its button from. Deliberately side-effect
    free: a mail gateway that fetches the link must be able to do so without
    moving the account anywhere, which is the whole reason the confirmation is a
    POST. Returns ``None`` for anything not usable, and the caller must not be
    able to tell an expired request from a cancelled one from a bad token.
    """
    token_hash = hash_token(raw_token or "")
    request = (
        EmailChangeRequest.objects.filter(current_token_hash=token_hash).first()
        or EmailChangeRequest.objects.filter(new_token_hash=token_hash).first()
    )
    return request if (request is not None and request.is_open) else None


def confirm(raw_token: str) -> EmailChangeRequest | None:
    """Record one confirmation. Applies the change when it is the second.

    Returns the request, or ``None`` when the token matches nothing usable — an
    expired request, a cancelled one, or a token that has already been used. The
    caller must not be able to tell those apart.
    """
    token_hash = hash_token(raw_token or "")

    with transaction.atomic():
        request = EmailChangeRequest.objects.select_for_update().filter(current_token_hash=token_hash).first()
        which = "current"

        if request is None:
            request = EmailChangeRequest.objects.select_for_update().filter(new_token_hash=token_hash).first()
            which = "new"

        if request is None or not request.is_open:
            logger.info("email_change.confirm_rejected")
            return None

        now = timezone.now()
        if which == "current":
            if request.confirmed_current_at is not None:
                return request
            request.confirmed_current_at = now
            request.save(update_fields=["confirmed_current_at"])
        else:
            if request.confirmed_new_at is not None:
                return request
            request.confirmed_new_at = now
            request.save(update_fields=["confirmed_new_at"])

        logger.info("email_change.confirmed_one_side", extra={"user_id": request.user_id, "side": which})

        if request.is_fully_confirmed:
            _apply(request)

        return request


def _apply(request: EmailChangeRequest) -> None:
    """Both sides are in. Move the address.

    Re-checks availability: an hour can pass between the two clicks, and an admin
    may have invited somebody onto the address in between. Failing here cancels the
    request rather than half-applying it.
    """
    user = request.user
    previous = user.email

    if User.objects.filter(email__iexact=request.new_email).exclude(pk=user.pk).exists():
        request.cancelled_at = timezone.now()
        request.save(update_fields=["cancelled_at"])
        logger.warning("email_change.address_taken", extra={"user_id": user.pk})
        return

    user.email = request.new_email
    user.save(update_fields=["email"])

    request.completed_at = timezone.now()
    request.save(update_fields=["completed_at"])

    AuditLog.objects.create(
        actor=user,
        action="account.email_changed",
        entity_type="User",
        entity_id=str(user.pk),
        before={"email": previous},
        after={"email": user.email},
    )
    logger.warning("email_change.applied", extra={"user_id": user.pk})

    # Tell the OLD address, after the fact and unconditionally. If this change was
    # not the owner's doing, this is the message that tells them — and it goes to
    # the address the attacker no longer controls.
    _notify_previous(previous, new_email=user.email)


def cancel(user, *, actor=None) -> bool:
    """Abandon a pending change. Returns whether there was one."""
    open_requests = user.email_changes.filter(completed_at__isnull=True, cancelled_at__isnull=True)
    cancelled = open_requests.update(cancelled_at=timezone.now())

    if cancelled:
        AuditLog.objects.create(
            actor=actor or user,
            action="account.email_change_cancelled",
            entity_type="User",
            entity_id=str(user.pk),
        )
    return bool(cancelled)


def _send(to: str, *, user, request: EmailChangeRequest, raw_token: str, which: str) -> None:
    path = reverse("accounts:email_change_confirm", kwargs={"token": raw_token})
    url = f"{settings.SITE_BASE_URL.rstrip('/')}{path}"

    body = render_to_string(
        "accounts/email/email_change.txt",
        {
            "url": url,
            "which": which,
            "current_email": user.email,
            "new_email": request.new_email,
            "hours": ttl_hours(),
        },
    )
    send_mail(
        subject="Confirm the change to your Kiam Clinic Directory email address",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[to],
        fail_silently=False,
    )


def _notify_previous(previous_email: str, *, new_email: str) -> None:
    body = render_to_string(
        "accounts/email/email_changed_notice.txt",
        {"previous_email": previous_email, "new_email": new_email},
    )
    send_mail(
        subject="Your Kiam Clinic Directory email address has been changed",
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[previous_email],
        fail_silently=True,
    )
