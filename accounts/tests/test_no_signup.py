"""There is no public signup route, and there must not become one.

Account creation is by admin ``Invite`` only, which arrives in Phase 2. This test
is here in Phase 0 because the failure it guards against is somebody adding a
"register" view later without realising it is a policy decision: an open signup
on a directory of verified clinicians is how unverified people get listings.
"""

from __future__ import annotations

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

FORBIDDEN_FRAGMENTS = ("signup", "sign-up", "register", "registration", "create-account")

pytestmark = pytest.mark.django_db


def _all_patterns(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            yield from _all_patterns(entry, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry.name


def test_no_url_looks_like_a_signup_route():
    offenders = [
        (route, name)
        for route, name in _all_patterns()
        if any(f in route.lower() or f in (name or "").lower() for f in FORBIDDEN_FRAGMENTS)
    ]
    assert offenders == [], (
        f"Found what looks like a signup route: {offenders}. Account creation is by "
        "admin Invite only — see docs/roadmap.md Phase 2."
    )


def test_the_login_page_offers_no_way_to_create_an_account(client):
    import re

    from django.urls import reverse

    body = client.get(reverse("accounts:login")).content.decode().lower()
    # Strip <script> blocks first: kiam-ui's accessibility panel has a comment
    # containing the word "registered", and a bare substring match on the whole
    # document would fail on somebody else's prose rather than on our own.
    visible = re.sub(r"<script\b.*?</script>", "", body, flags=re.DOTALL)

    for phrase in ("sign up", "create an account", "register for", "new account"):
        assert phrase not in visible


def test_users_have_an_unusable_password(practitioner, admin_user, verifier):
    """Sign-in is a magic link. A settable password would be a second way in."""
    for user in (practitioner, admin_user, verifier):
        assert not user.has_usable_password()


def test_createsuperuser_is_the_one_password_and_it_is_not_a_bypass(db, client, settings):
    """The authored UserManager gives a superuser a password on purpose.

    Django's own admin login form needs one. That is not a way around the magic
    link: the admin sits behind TwoFactorEnforcementMiddleware, so a password
    alone gets as far as the TOTP challenge and no further.
    """
    from accounts.models import User

    user = User.objects.create_superuser(email="root@example.com", password="pw")
    assert user.has_usable_password()
    assert user.role == User.Role.SUPERADMIN

    client.force_login(user)
    response = client.get(f"/{settings.ADMIN_URL_PATH}/")

    assert response.status_code == 302
    assert response.url.startswith("/accounts/two-factor/")
