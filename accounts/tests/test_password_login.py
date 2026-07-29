"""Password sign-in, alongside the magic link.

The negative tests are the ones that matter. Adding passwords to a directory of
named clinicians reopens two attacks the magic-link-only design did not have —
account enumeration and credential stuffing — so most of this file is about
those rather than about the happy path.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from accounts.models import User
from accounts.services import passwords

pytestmark = pytest.mark.django_db

GOOD = "correct horse battery staple"
LOGIN = "/accounts/login/"


def _comparable(response) -> str:
    """Response body with the two things that legitimately differ masked out.

    1. The CSRF token — appears twice, in the form and in kiam-ui's hx-headers.
    2. The email the visitor typed, echoed back so they can correct it.

    Neither reveals anything: the token is per-request, and the address is what
    they just entered. What must NOT differ is everything else — the message,
    the status, the shape of the page.
    """
    import re

    body = response.content.decode()
    body = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "CSRF", body)
    body = re.sub(r"X-CSRFToken&quot;: &quot;[^&]*&quot;", "CSRF", body)
    body = re.sub(r'X-CSRFToken": "[^"]*"', "CSRF", body)
    return re.sub(r'value="[^"]*@[^"]*"', 'value="ECHOED"', body)


@pytest.fixture
def with_password(practitioner):
    passwords.set_password(practitioner, GOOD)
    return practitioner


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_password_signs_you_in(client, with_password):
    response = client.post(LOGIN, {"email": with_password.email, "password": GOOD})

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(with_password.pk)


def test_signing_in_updates_last_login_at(client, with_password):
    client.post(LOGIN, {"email": with_password.email, "password": GOOD})

    with_password.refresh_from_db()
    assert with_password.last_login_at is not None


def test_the_email_is_matched_case_insensitively(client, with_password):
    response = client.post(LOGIN, {"email": with_password.email.upper(), "password": GOOD})

    assert response.status_code == 302
    assert "_auth_user_id" in client.session


# ---------------------------------------------------------------------------
# Both routes still work
# ---------------------------------------------------------------------------


def test_leaving_the_password_blank_still_emails_a_link(client, practitioner):
    from django.core import mail

    response = client.post(LOGIN, {"email": practitioner.email, "password": ""})

    assert response.status_code == 302
    assert response.url == reverse("accounts:login_sent")
    assert len(mail.outbox) == 1


def test_an_account_with_no_password_can_still_use_the_link(client, practitioner):
    from django.core import mail

    assert not practitioner.has_usable_password()

    client.post(LOGIN, {"email": practitioner.email, "password": ""})

    assert len(mail.outbox) == 1


def test_the_login_page_offers_both(client):
    body = client.get(LOGIN).content.decode()

    assert 'name="email"' in body
    assert 'type="password"' in body
    assert "emailed link" in body


# ---------------------------------------------------------------------------
# No account enumeration — the property passwords most easily break
# ---------------------------------------------------------------------------


def test_a_wrong_password_and_an_unknown_address_are_indistinguishable(client, with_password):
    wrong = client.post(LOGIN, {"email": with_password.email, "password": "not the password"})
    unknown = client.post(LOGIN, {"email": "nobody@example.com", "password": "not the password"})

    assert wrong.status_code == unknown.status_code == 401
    assert _comparable(wrong) == _comparable(unknown)


def test_an_account_with_no_password_fails_like_a_wrong_password(client, practitioner):
    """Not "this account has no password" — that names accounts to go after."""
    no_password = client.post(LOGIN, {"email": practitioner.email, "password": "anything at all"})
    unknown = client.post(LOGIN, {"email": "nobody@example.com", "password": "anything at all"})

    assert no_password.status_code == unknown.status_code == 401
    assert _comparable(no_password) == _comparable(unknown)


def test_a_deactivated_account_fails_like_a_wrong_password(client, with_password):
    with_password.is_active = False
    with_password.save(update_fields=["is_active"])

    response = client.post(LOGIN, {"email": with_password.email, "password": GOOD})

    assert response.status_code == 401
    assert "_auth_user_id" not in client.session


def test_the_failure_message_names_neither_field(client, with_password):
    body = client.post(LOGIN, {"email": with_password.email, "password": "wrong"}).content.decode()

    # The apostrophe is HTML-escaped in the rendered page.
    assert "match" in body and "email address and password" in body
    for leak in ("no password", "not found", "no account", "incorrect password"):
        assert leak not in body.lower()


# ---------------------------------------------------------------------------
# Credential stuffing
# ---------------------------------------------------------------------------


def test_attempts_are_rate_limited_per_email(client, with_password, settings):
    settings.PASSWORD_LOGIN_MAX_PER_EMAIL = 3

    for _ in range(3):
        client.post(LOGIN, {"email": with_password.email, "password": "wrong"})

    response = client.post(LOGIN, {"email": with_password.email, "password": "wrong"})

    assert response.status_code == 429
    assert b"Too many sign-in attempts" in response.content


def test_the_email_limit_applies_to_unknown_addresses_too(client, settings):
    """Otherwise the limiter is itself the oracle: unlimited tries means no account."""
    settings.PASSWORD_LOGIN_MAX_PER_EMAIL = 2

    for _ in range(2):
        client.post(LOGIN, {"email": "nobody@example.com", "password": "x"})

    response = client.post(LOGIN, {"email": "nobody@example.com", "password": "x"})
    assert response.status_code == 429


def test_the_ip_limit_catches_stuffing_across_many_accounts(client, user_factory, settings):
    """The attack a per-email limit cannot see: one password, many addresses."""
    settings.PASSWORD_LOGIN_MAX_PER_IP = 4

    for i in range(4):
        user_factory(f"target{i}@example.com")
        client.post(LOGIN, {"email": f"target{i}@example.com", "password": "Password123!"})

    user_factory("target99@example.com")
    response = client.post(LOGIN, {"email": "target99@example.com", "password": "Password123!"})

    assert response.status_code == 429


def test_a_rate_limited_response_reveals_nothing_about_the_account(client, with_password, settings):
    settings.PASSWORD_LOGIN_MAX_PER_EMAIL = 1

    client.post(LOGIN, {"email": with_password.email, "password": "wrong"})
    known = client.post(LOGIN, {"email": with_password.email, "password": "wrong"})

    client.post(LOGIN, {"email": "nobody@example.com", "password": "wrong"})
    unknown = client.post(LOGIN, {"email": "nobody@example.com", "password": "wrong"})

    assert known.status_code == unknown.status_code == 429
    assert _comparable(known) == _comparable(unknown)


def test_a_successful_sign_in_clears_the_email_budget(client, with_password, settings):
    """Ten correct sign-ins in a morning must not lock somebody out."""
    settings.PASSWORD_LOGIN_MAX_PER_EMAIL = 3

    for _ in range(5):
        client.post(LOGIN, {"email": with_password.email, "password": GOOD})
        client.post(reverse("accounts:logout"))

    assert client.post(LOGIN, {"email": with_password.email, "password": GOOD}).status_code == 302


# ---------------------------------------------------------------------------
# Setting a password
# ---------------------------------------------------------------------------


def _sign_in_by_link(client, user):
    from accounts.services import magic_link

    _, raw = magic_link.issue(user)
    return client.get(reverse("accounts:magic_link_consume", kwargs={"token": raw}))


def test_setting_a_password_needs_a_session(client):
    response = client.get(reverse("accounts:set_password"))

    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_a_magic_link_session_can_set_a_password(client, practitioner):
    """The whole recovery story: forgotten it, sign in by link, set a new one."""
    _sign_in_by_link(client, practitioner)

    response = client.post(
        reverse("accounts:set_password"),
        {"new_password": GOOD, "confirm_password": GOOD},
    )

    assert response.status_code == 302
    practitioner.refresh_from_db()
    assert practitioner.check_password(GOOD)


def test_setting_a_password_does_not_log_you_out(client, practitioner):
    """Django rotates the session auth hash on a password change."""
    _sign_in_by_link(client, practitioner)

    client.post(reverse("accounts:set_password"), {"new_password": GOOD, "confirm_password": GOOD})

    assert client.get(reverse("accounts:set_password")).status_code == 200


def test_a_mismatched_confirmation_is_rejected(client, practitioner):
    _sign_in_by_link(client, practitioner)

    response = client.post(
        reverse("accounts:set_password"),
        {"new_password": GOOD, "confirm_password": "something else entirely"},
    )

    assert response.status_code == 200
    practitioner.refresh_from_db()
    assert not practitioner.has_usable_password()


@pytest.mark.parametrize("weak", ["short", "password1234", "123456789012", "qwertyuiop12"])
def test_weak_passwords_are_refused(client, practitioner, weak):
    _sign_in_by_link(client, practitioner)

    client.post(reverse("accounts:set_password"), {"new_password": weak, "confirm_password": weak})

    practitioner.refresh_from_db()
    assert not practitioner.has_usable_password(), f"accepted a weak password: {weak!r}"


def test_a_password_too_similar_to_the_email_is_refused(client, practitioner):
    _sign_in_by_link(client, practitioner)
    similar = practitioner.email.split("@")[0] + "12345"

    client.post(reverse("accounts:set_password"), {"new_password": similar, "confirm_password": similar})

    practitioner.refresh_from_db()
    assert not practitioner.has_usable_password()


def test_a_password_can_be_removed(client, with_password):
    _sign_in_by_link(client, with_password)

    client.post(reverse("accounts:set_password"), {"action": "remove"})

    with_password.refresh_from_db()
    assert not with_password.has_usable_password()


def test_removing_a_password_leaves_the_magic_link_working(client, with_password):
    from django.core import mail

    passwords.clear_password(with_password)
    mail.outbox.clear()

    client.post(LOGIN, {"email": with_password.email, "password": ""})

    assert len(mail.outbox) == 1


def test_the_set_password_page_is_noindex(client, practitioner):
    _sign_in_by_link(client, practitioner)

    body = client.get(reverse("accounts:set_password")).content.decode()

    assert 'content="noindex, nofollow"' in body


# ---------------------------------------------------------------------------
# Interaction with the rest of the auth design
# ---------------------------------------------------------------------------


def test_a_password_does_not_prove_control_of_the_mailbox(client, with_password):
    """email_verified_at is set by redeeming a link, and only by that."""
    assert with_password.email_verified_at is None

    client.post(LOGIN, {"email": with_password.email, "password": GOOD})

    with_password.refresh_from_db()
    assert with_password.email_verified_at is None


def test_staff_signing_in_with_a_password_still_hit_the_totp_wall(client, user_factory):
    """A password must not be a way around the second factor."""
    admin = user_factory("staffer@example.com", User.Role.ADMIN)
    passwords.set_password(admin, GOOD)

    response = client.post(LOGIN, {"email": admin.email, "password": GOOD})

    assert response.status_code == 302
    assert "/accounts/two-factor/" in response.url

    blocked = client.get(reverse("pages:home"))
    assert blocked.status_code == 302
    assert "/accounts/two-factor/" in blocked.url


def test_accounts_are_still_created_without_a_password(user_factory):
    """Passwords are opt-in. Nothing sets one on your behalf."""
    user = user_factory("fresh@example.com")
    assert not user.has_usable_password()


def test_an_invited_practitioner_starts_with_no_password(admin_user):
    from backoffice.services import invites

    _, raw = invites.issue("invited@example.com", invited_by=admin_user)
    user = invites.accept(raw)

    assert not user.has_usable_password()


def test_the_honeypot_still_swallows_bots_on_the_password_route(client, with_password):
    response = client.post(
        LOGIN,
        {"email": with_password.email, "password": GOOD, "website": "http://spam.example"},
    )

    assert response.url == reverse("accounts:login_sent")
    assert "_auth_user_id" not in client.session


def test_production_hashes_with_argon2():
    """Read from config.settings.base, not the active test settings.

    config/settings/test.py pins MD5 so the suite is not spending a second per
    password on a memory-hard KDF. That means `settings.PASSWORD_HASHERS` here
    describes the test run, not production — so this asserts on the base module
    directly, which is the thing that ships.
    """
    from config.settings import base

    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


def test_argon2_is_actually_installed():
    """The hasher is configured first, so a missing dependency breaks logins."""
    from django.contrib.auth.hashers import Argon2PasswordHasher

    hashed = Argon2PasswordHasher().encode(GOOD, salt="testsalt")
    assert hashed.startswith("argon2")
