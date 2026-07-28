"""TOTP enrolment and verification for staff roles.

``django_otp`` supplies the device model and the verification maths; it does not
own the login flow. The order here is deliberate:

    magic link  ->  session established  ->  TOTP challenge  ->  session marked verified

A staff member is *authenticated* before the second factor and *authorised* only
after it. ``accounts.middleware.TwoFactorEnforcementMiddleware`` is what makes
that hold: it refuses to let an unverified staff session reach any page except
the challenge, so a capability predicate in ``accounts.access`` is only ever
evaluated on a session that has already cleared 2FA.

``User.totp_enabled`` is the authored model's flag for this. It is kept in step
with the device here rather than being settable on its own — a boolean that can
drift from whether a device actually exists is worse than no boolean at all.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from ..access import requires_two_factor

logger = logging.getLogger("accounts")

#: One device per person. A second confirmed device is an unaudited backdoor into
#: a staff account; re-enrolment replaces rather than adds.
DEVICE_NAME = "default"


def get_device(user) -> TOTPDevice | None:
    """The user's confirmed device, if they have one."""
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def has_device(user) -> bool:
    return get_device(user) is not None


def needs_enrolment(user) -> bool:
    """A staff account that must carry a device but has not enrolled one yet."""
    return requires_two_factor(user) and not has_device(user)


def start_enrolment(user) -> TOTPDevice:
    """Create (or reuse) an *unconfirmed* device and return it.

    Unconfirmed until the user proves they can read a code from it — otherwise a
    mistyped secret locks them out of their own account with no way back.
    """
    device, created = TOTPDevice.objects.get_or_create(
        user=user, confirmed=False, defaults={"name": DEVICE_NAME}
    )
    if created:
        logger.info("two_factor.enrolment_started", extra={"user_id": user.pk})
    return device


def provisioning_uri(device: TOTPDevice) -> str:
    """The ``otpauth://`` URI an authenticator app scans."""
    return device.config_url


def confirm_enrolment(request, user, device: TOTPDevice, code: str) -> bool:
    """Verify the first code, make the device live, and verify the session.

    The session is marked verified HERE rather than by a follow-up ``verify()``
    call. TOTP replay protection refuses a code the device has already accepted,
    so re-presenting the same six digits fails — and the user who has just
    successfully enrolled would be bounced straight to the challenge page and
    have to wait out the step for a fresh code. Confirming the code *is* the
    proof; there is nothing further to prove.
    """
    if not device.verify_token(code):
        logger.warning("two_factor.enrolment_failed", extra={"user_id": user.pk})
        return False

    # Replace rather than accumulate — see DEVICE_NAME.
    TOTPDevice.objects.filter(user=user, confirmed=True).exclude(pk=device.pk).delete()
    device.confirmed = True
    device.name = DEVICE_NAME
    device.save(update_fields=["confirmed", "name"])

    _sync_flag(user, True)
    otp_login(request, device)
    logger.info("two_factor.enrolled", extra={"user_id": user.pk})
    return True


def verify(request, user, code: str) -> bool:
    """Check a code against the confirmed device and mark the session verified.

    ``django_otp.login`` is what writes the verified device onto the session, so
    ``request.user.otp_device`` — and therefore
    ``accounts.access.has_verified_two_factor`` — is true for the rest of it.
    """
    device = get_device(user)
    if device is None:
        return False

    # verify_token() enforces TOTP replay protection: a code already used by this
    # device is refused, so shoulder-surfing one code buys nothing.
    if not device.verify_token(code):
        logger.warning("two_factor.verify_failed", extra={"user_id": user.pk})
        return False

    otp_login(request, device)
    logger.info("two_factor.verified", extra={"user_id": user.pk})
    return True


def _sync_flag(user, enabled: bool) -> None:
    """Keep ``User.totp_enabled`` truthful about whether a device exists."""
    if user.totp_enabled != enabled:
        user.totp_enabled = enabled
        user.save(update_fields=["totp_enabled"])


def issuer() -> str:
    return getattr(settings, "OTP_TOTP_ISSUER", "Kiam Clinic Directory")
