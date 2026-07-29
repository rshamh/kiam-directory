"""The login views, end to end through the test client.

The response-identity tests are the important ones: a difference of any kind
between "this address has an account" and "it doesn't" is an account oracle, and
this directory lists named clinicians whose addresses are semi-public.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from accounts.models import LoginToken
from accounts.services import magic_link

pytestmark = pytest.mark.django_db


def test_login_page_renders(client):
    """Both routes on one page.

    The password field became optional when password sign-in was added alongside
    the magic link. This test used to assert there was no password field
    anywhere; that is now deliberately false. See
    accounts/tests/test_password_login.py.
    """
    response = client.get(reverse("accounts:login"))

    assert response.status_code == 200
    assert b'name="email"' in response.content
    assert b'name="password"' in response.content
    # Still no signup link — account creation is by admin invite only.
    assert b"create an account" not in response.content.lower()


def test_login_page_is_noindex(client):
    response = client.get(reverse("accounts:login"))
    assert b'content="noindex, nofollow"' in response.content


def test_known_and_unknown_addresses_get_identical_responses(client, practitioner):
    known = client.post(reverse("accounts:login"), {"email": practitioner.email})
    unknown = client.post(reverse("accounts:login"), {"email": "nobody@example.com"})

    assert known.status_code == unknown.status_code == 302
    assert known.url == unknown.url == reverse("accounts:login_sent")


def test_the_confirmation_page_does_not_confirm_anything(client):
    response = client.get(reverse("accounts:login_sent"))

    assert response.status_code == 200
    # "If that address has an account" — not "we've sent you an email".
    assert b"If that address has an account" in response.content


def test_a_full_login_establishes_a_session(client, practitioner):
    client.post(reverse("accounts:login"), {"email": practitioner.email})
    token = LoginToken.objects.get(user=practitioner)

    # The raw token is not recoverable from the row, so mint a known one.
    _, raw = magic_link.issue(practitioner)
    response = client.get(reverse("accounts:magic_link_consume", kwargs={"token": raw}))

    assert response.status_code == 302
    assert client.session["_auth_user_id"] == str(practitioner.pk)
    assert token.pk  # the earlier token existed


def test_login_updates_last_login_at(client, practitioner):
    _, raw = magic_link.issue(practitioner)
    client.get(reverse("accounts:magic_link_consume", kwargs={"token": raw}))

    practitioner.refresh_from_db()
    assert practitioner.last_login_at is not None


def test_a_spent_link_gives_410(client, practitioner):
    _, raw = magic_link.issue(practitioner)
    url = reverse("accounts:magic_link_consume", kwargs={"token": raw})

    client.get(url)
    client.logout()
    response = client.get(url)

    assert response.status_code == 410


def test_the_honeypot_silently_swallows_a_bot(client, practitioner):
    response = client.post(
        reverse("accounts:login"),
        {"email": practitioner.email, "website": "http://spam.example"},
    )

    # Same redirect a human gets — a bot learns nothing — but no token minted.
    assert response.url == reverse("accounts:login_sent")
    assert LoginToken.objects.count() == 0


def test_rate_limiting_returns_429_without_revealing_the_account(client, settings):
    settings.MAGIC_LINK_MAX_PER_EMAIL = 1

    client.post(reverse("accounts:login"), {"email": "nobody@example.com"})
    response = client.post(reverse("accounts:login"), {"email": "nobody@example.com"})

    assert response.status_code == 429
    assert b"Too many sign-in requests" in response.content


def test_logout_requires_post(client, practitioner):
    _, raw = magic_link.issue(practitioner)
    client.get(reverse("accounts:magic_link_consume", kwargs={"token": raw}))

    assert client.get(reverse("accounts:logout")).status_code == 405

    response = client.post(reverse("accounts:logout"))
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session


def test_an_authenticated_visitor_is_redirected_off_the_login_page(client, practitioner):
    _, raw = magic_link.issue(practitioner)
    client.get(reverse("accounts:magic_link_consume", kwargs={"token": raw}))

    response = client.get(reverse("accounts:login"))
    assert response.status_code == 302
