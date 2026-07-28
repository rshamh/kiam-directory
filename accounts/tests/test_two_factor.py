"""TOTP enrolment, verification, and the middleware that enforces both."""

from __future__ import annotations

import pytest
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.services import magic_link, two_factor

pytestmark = pytest.mark.django_db


def _current_code(device: TOTPDevice) -> str:
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits)
    return str(totp.token()).zfill(device.digits)


def _sign_in(client, user):
    _, raw = magic_link.issue(user)
    return client.get(reverse("accounts:magic_link_consume", kwargs={"token": raw}))


def _request(user):
    """A request with a session, for calling the service directly.

    ``confirm_enrolment`` marks the session verified, so it needs a real session
    to write to — the same one the view would hand it.
    """
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    request = RequestFactory().post("/accounts/two-factor/set-up/")
    request.session = SessionStore()
    request.user = user
    return request


def _enrol(user) -> TOTPDevice:
    """Take ``user`` through enrolment and return the confirmed device."""
    device = two_factor.start_enrolment(user)
    two_factor.confirm_enrolment(_request(user), user, device, _current_code(device))
    return device


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------


def test_a_staff_role_needs_enrolment(admin_user, practitioner):
    assert two_factor.needs_enrolment(admin_user)
    assert not two_factor.needs_enrolment(practitioner)


def test_enrolment_starts_unconfirmed(admin_user):
    """A mistyped secret must not lock someone out of their own account."""
    device = two_factor.start_enrolment(admin_user)

    assert device.confirmed is False
    assert not two_factor.has_device(admin_user)


def test_a_correct_code_confirms_the_device(admin_user):
    device = two_factor.start_enrolment(admin_user)

    assert two_factor.confirm_enrolment(_request(admin_user), admin_user, device, _current_code(device))
    assert two_factor.has_device(admin_user)


def test_a_wrong_code_does_not_confirm(admin_user):
    device = two_factor.start_enrolment(admin_user)

    assert not two_factor.confirm_enrolment(_request(admin_user), admin_user, device, "000000")
    assert not two_factor.has_device(admin_user)


def test_re_enrolment_replaces_rather_than_adds(admin_user):
    """A second confirmed device is an unaudited backdoor into a staff account."""
    _enrol(admin_user)

    second = TOTPDevice.objects.create(user=admin_user, confirmed=False, name="second")
    two_factor.confirm_enrolment(_request(admin_user), admin_user, second, _current_code(second))

    assert TOTPDevice.objects.filter(user=admin_user, confirmed=True).count() == 1


# ---------------------------------------------------------------------------
# The enforcement middleware
# ---------------------------------------------------------------------------


def test_staff_are_sent_to_enrolment_after_signing_in(client, admin_user):
    response = _sign_in(client, admin_user)
    assert response.url == reverse("accounts:two_factor_setup")


def test_an_unverified_staff_session_cannot_reach_the_site(client, admin_user):
    _sign_in(client, admin_user)

    response = client.get(reverse("pages:home"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:two_factor_setup")


def test_an_unverified_staff_session_cannot_reach_the_django_admin(client, superadmin, settings):
    _sign_in(client, superadmin)

    response = client.get(f"/{settings.ADMIN_URL_PATH}/")

    assert response.status_code == 302
    assert response.url.startswith("/accounts/two-factor/")


def test_a_practitioner_is_not_challenged(client, practitioner):
    response = _sign_in(client, practitioner)
    assert response.url == reverse("pages:home")

    assert client.get(reverse("pages:home")).status_code == 200


def test_healthz_stays_reachable_for_an_unverified_staff_session(client, admin_user):
    """A health check that starts redirecting takes the instance out of the pool."""
    _sign_in(client, admin_user)
    assert client.get(reverse("seo:healthz")).status_code == 200


def test_logout_stays_reachable_for_an_unverified_staff_session(client, admin_user):
    _sign_in(client, admin_user)
    assert client.post(reverse("accounts:logout")).status_code == 302


def test_completing_enrolment_unblocks_the_session(client, admin_user):
    _sign_in(client, admin_user)

    setup = client.get(reverse("accounts:two_factor_setup"))
    assert setup.status_code == 200

    device = TOTPDevice.objects.get(user=admin_user, confirmed=False)
    response = client.post(reverse("accounts:two_factor_setup"), {"code": _current_code(device)})

    assert response.status_code == 302
    assert client.get(reverse("pages:home")).status_code == 200


def test_a_returning_staff_member_gets_the_challenge_not_enrolment(client, admin_user):
    _enrol(admin_user)

    response = _sign_in(client, admin_user)
    assert response.url == reverse("accounts:two_factor_verify")


def test_a_practitioner_cannot_reach_the_two_factor_pages(client, practitioner):
    _sign_in(client, practitioner)

    assert client.get(reverse("accounts:two_factor_setup")).status_code == 404
    assert client.get(reverse("accounts:two_factor_verify")).status_code == 404


def test_the_qr_view_serves_an_svg_and_is_never_cached(client, admin_user):
    """The image carries the TOTP secret — it must not sit in a shared cache."""
    _sign_in(client, admin_user)
    two_factor.start_enrolment(admin_user)

    response = client.get(reverse("accounts:two_factor_qr"))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml"
    assert "no-store" in response["Cache-Control"]


def test_the_qr_view_404s_once_the_device_is_confirmed(client, admin_user):
    _enrol(admin_user)
    _sign_in(client, admin_user)

    assert client.get(reverse("accounts:two_factor_qr")).status_code == 404
