"""Role predicates.

``accounts/access.py`` is the only place role checks live, so it is the only
place they need testing — and the only place they *can* be tested once. The
admin/verifier split and the 2FA gate are both real controls; each has a test
that fails if someone widens it.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from accounts import access
from accounts.models import Role

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_anonymous_is_nothing():
    anon = AnonymousUser()

    assert not access.is_practitioner(anon)
    assert not access.is_staff_role(anon)
    assert not access.can_access_dashboard(anon)
    assert not access.can_view_private_evidence(anon)
    assert not access.two_factor_satisfied(anon)


def test_a_deactivated_user_has_no_role(practitioner):
    practitioner.is_active = False
    assert not access.is_practitioner(practitioner)
    assert not access.can_access_dashboard(practitioner)


def test_is_admin_is_exact_not_or_above(admin_user, verifier, superadmin):
    assert access.is_admin(admin_user)
    assert not access.is_admin(verifier)
    assert not access.is_admin(superadmin)


def test_is_staff_role_covers_the_three_kiam_roles(practitioner, admin_user, verifier, superadmin):
    assert not access.is_staff_role(practitioner)
    assert access.is_staff_role(admin_user)
    assert access.is_staff_role(verifier)
    assert access.is_staff_role(superadmin)


# ---------------------------------------------------------------------------
# The admin / verifier split — CLAUDE.md and docs/architecture.md
# ---------------------------------------------------------------------------


def test_admin_cannot_open_private_evidence(admin_user, verified_2fa):
    """Approving profile copy and inspecting a passport scan are different jobs.

    If this test ever fails, the control it protects is gone: widening the
    predicate widens who can read scans of people's identity documents.
    """
    verified_2fa(admin_user)
    assert access.can_review_submissions(admin_user)
    assert not access.can_view_private_evidence(admin_user)
    assert not access.can_grant_provisional_dbs(admin_user)


def test_verifier_can_open_private_evidence(verifier, verified_2fa):
    verified_2fa(verifier)
    assert access.can_view_private_evidence(verifier)
    assert access.can_grant_provisional_dbs(verifier)


def test_practitioner_cannot_open_private_evidence(practitioner):
    assert not access.can_view_private_evidence(practitioner)


def test_only_superadmin_reaches_django_admin(practitioner, admin_user, verifier, superadmin, verified_2fa):
    for user in (admin_user, verifier, superadmin):
        verified_2fa(user)

    assert not access.can_use_django_admin(practitioner)
    assert not access.can_use_django_admin(admin_user)
    assert not access.can_use_django_admin(verifier)
    assert access.can_use_django_admin(superadmin)


# ---------------------------------------------------------------------------
# Two-factor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "required"),
    [
        (Role.PRACTITIONER, False),
        (Role.ADMIN, True),
        (Role.VERIFIER, True),
        (Role.SUPERADMIN, True),
    ],
)
def test_which_roles_require_two_factor(user_factory, role, required):
    user = user_factory(f"{role}@example.com", role)
    assert access.requires_two_factor(user) is required


def test_every_staff_capability_is_gated_on_two_factor(admin_user, verifier, superadmin):
    """Unverified staff sessions get nothing. No exceptions, no partial access."""
    for user in (admin_user, verifier, superadmin):
        assert not access.two_factor_satisfied(user)
        assert not access.can_access_backoffice(user)
        assert not access.can_review_submissions(user)
        assert not access.can_invite_users(user)
        assert not access.can_suspend_listing(user)
        assert not access.can_manage_taxonomy(user)
        assert not access.can_view_private_evidence(user)
        assert not access.can_use_django_admin(user)


def test_a_practitioner_needs_no_second_factor(practitioner):
    assert access.two_factor_satisfied(practitioner)
    assert access.can_access_dashboard(practitioner)


def test_verifying_the_session_unlocks_staff_capabilities(verifier, verified_2fa):
    assert not access.can_access_backoffice(verifier)
    verified_2fa(verifier)
    assert access.can_access_backoffice(verifier)


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def test_require_sends_anonymous_users_to_login(rf):
    from django.http import HttpResponse

    @access.require(access.can_access_backoffice)
    def view(request):  # pragma: no cover - never reached
        return HttpResponse("secret")

    request = rf.get("/backoffice/")
    request.user = AnonymousUser()

    response = view(request)
    assert response.status_code == 302
    assert "/accounts/login/" in response.url


def test_require_403s_a_signed_in_user_who_lacks_the_capability(rf, practitioner):
    """Not a redirect: a redirect would tell them the URL exists."""
    from django.core.exceptions import PermissionDenied
    from django.http import HttpResponse

    @access.require(access.can_view_private_evidence)
    def view(request):  # pragma: no cover - never reached
        return HttpResponse("evidence")

    request = rf.get("/evidence/1/")
    request.user = practitioner

    with pytest.raises(PermissionDenied):
        view(request)


def test_require_lets_a_permitted_user_through(rf, verifier, verified_2fa):
    from django.http import HttpResponse

    @access.require(access.can_view_private_evidence)
    def view(request):
        return HttpResponse("evidence")

    request = rf.get("/evidence/1/")
    request.user = verified_2fa(verifier)

    assert view(request).status_code == 200
