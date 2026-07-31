"""Role predicates.

``accounts/access.py`` is the only place role checks live, so it is the only
place they need testing — and the only place they *can* be tested once.

Two things it asserts that are real controls rather than plumbing:

* the **admin / verifier split** — approving profile copy and opening someone's
  passport scan are different jobs;
* **``can_manage_taxonomy`` excludes VERIFIER** — narrower than "any staff role",
  and easy to widen by accident when someone reaches for ``is_staff_role``.

The 2FA helpers are tested here as pure functions. The *enforcement* is the
middleware's job and is tested in test_two_factor.py — see the note in access.py
on why the two are kept apart.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser

from apps.accounts import access
from apps.accounts.models import User

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_anonymous_is_nothing():
    anon = AnonymousUser()

    assert not access.is_staff_role(anon)
    assert not access.can_review_submissions(anon)
    assert not access.can_view_evidence(anon)
    assert not access.can_manage_taxonomy(anon)
    assert not access.two_factor_satisfied(anon)


def test_is_staff_role_covers_the_three_kiam_roles(practitioner, admin_user, verifier, superadmin):
    assert not access.is_staff_role(practitioner)
    assert access.is_staff_role(admin_user)
    assert access.is_staff_role(verifier)
    assert access.is_staff_role(superadmin)


def test_a_practitioner_cannot_review(practitioner):
    assert not access.can_review_submissions(practitioner)


# ---------------------------------------------------------------------------
# The admin / verifier split — CLAUDE.md and docs/architecture.md
# ---------------------------------------------------------------------------


def test_admin_cannot_open_private_evidence(admin_user):
    """Approving profile copy and inspecting a passport scan are different jobs.

    If this test ever fails, the control it protects is gone: widening the
    predicate widens who can read scans of people's identity documents.
    """
    assert access.can_review_submissions(admin_user)
    assert not access.can_view_evidence(admin_user)
    assert not access.can_grant_provisional_dbs(admin_user)
    assert not access.can_extend_provisional_window(admin_user)


def test_verifier_can_open_private_evidence(verifier):
    assert access.can_view_evidence(verifier)
    assert access.can_grant_provisional_dbs(verifier)
    assert access.can_extend_provisional_window(verifier)


def test_superadmin_can_open_private_evidence(superadmin):
    assert access.can_view_evidence(superadmin)


def test_practitioner_cannot_open_private_evidence(practitioner):
    assert not access.can_view_evidence(practitioner)


# ---------------------------------------------------------------------------
# Taxonomy — deliberately NOT "any staff role"
# ---------------------------------------------------------------------------


def test_taxonomy_is_admin_and_superadmin_only(practitioner, admin_user, verifier, superadmin):
    """A verifier checks documents; they do not curate the vocabulary.

    Note this is narrower than can_review_submissions, which a verifier DOES
    have. Reaching for is_staff_role here would silently widen it.
    """
    assert not access.can_manage_taxonomy(practitioner)
    assert access.can_manage_taxonomy(admin_user)
    assert not access.can_manage_taxonomy(verifier)
    assert access.can_manage_taxonomy(superadmin)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


class _FakePractitioner:
    """Stands in for directory.Practitioner, which arrives in Phase 1."""

    def __init__(self, user_id):
        self.user_id = user_id


def test_owns_practitioner_matches_on_user_id(practitioner, user_factory):
    other = user_factory("other@example.com")

    assert access.owns_practitioner(practitioner, _FakePractitioner(practitioner.id))
    assert not access.owns_practitioner(other, _FakePractitioner(practitioner.id))


def test_staff_can_edit_someone_elses_profile(practitioner, admin_user):
    profile = _FakePractitioner(practitioner.id)

    assert access.can_edit_practitioner(practitioner, profile)
    assert access.can_edit_practitioner(admin_user, profile)


def test_a_practitioner_cannot_edit_another(practitioner, user_factory):
    other = user_factory("other@example.com")
    assert not access.can_edit_practitioner(other, _FakePractitioner(practitioner.id))


# ---------------------------------------------------------------------------
# Two-factor helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "required"),
    [
        (User.Role.PRACTITIONER, False),
        (User.Role.ADMIN, True),
        (User.Role.VERIFIER, True),
        (User.Role.SUPERADMIN, True),
    ],
)
def test_which_roles_require_two_factor(user_factory, role, required):
    user = user_factory(f"{role}@example.com", role)
    assert access.requires_two_factor(user) is required


def test_an_unverified_staff_session_does_not_satisfy_the_gate(admin_user, verifier, superadmin):
    for user in (admin_user, verifier, superadmin):
        assert not access.has_verified_two_factor(user)
        assert not access.two_factor_satisfied(user)


def test_a_verified_staff_session_satisfies_the_gate(verifier, verified_2fa):
    verified_2fa(verifier)
    assert access.has_verified_two_factor(verifier)
    assert access.two_factor_satisfied(verifier)


def test_a_practitioner_needs_no_second_factor(practitioner):
    assert not access.requires_two_factor(practitioner)
    assert access.two_factor_satisfied(practitioner)


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def test_require_403s_a_user_without_the_capability(rf, practitioner):
    from django.core.exceptions import PermissionDenied
    from django.http import HttpResponse

    @access.require(access.can_view_evidence)
    def view(request):  # pragma: no cover - never reached
        return HttpResponse("evidence")

    request = rf.get("/evidence/1/")
    request.user = practitioner

    with pytest.raises(PermissionDenied):
        view(request)


def test_require_403s_anonymous(rf):
    from django.core.exceptions import PermissionDenied
    from django.http import HttpResponse

    @access.require(access.can_review_submissions)
    def view(request):  # pragma: no cover - never reached
        return HttpResponse("queue")

    request = rf.get("/backoffice/")
    request.user = AnonymousUser()

    with pytest.raises(PermissionDenied):
        view(request)


def test_require_lets_a_permitted_user_through(rf, verifier):
    from django.http import HttpResponse

    @access.require(access.can_view_evidence)
    def view(request):
        return HttpResponse("evidence")

    request = rf.get("/evidence/1/")
    request.user = verifier

    assert view(request).status_code == 200


# ---------------------------------------------------------------------------
# The session's authentication backend
# ---------------------------------------------------------------------------


def test_the_login_backend_path_is_one_django_will_accept():
    """A backend path that is not in settings signs nobody in, and says nothing.

    `login(request, user, backend=...)` writes the string into the session and
    `auth.get_user()` checks it against `AUTHENTICATION_BACKENDS` — if it is not
    there it returns AnonymousUser with no exception and no log line. Both sign-in
    routes passed a hand-written literal until the apps moved under `apps/`, at
    which point the magic link was consumed, the redirect happened, and the next
    page was anonymous.

    `BACKEND_PATH` is derived from the class, so this asserts the settings entry
    agrees with where the class actually lives — not that two strings match.
    """
    from django.conf import settings

    from apps.accounts.backends import BACKEND_PATH

    assert BACKEND_PATH in settings.AUTHENTICATION_BACKENDS


def test_no_sign_in_route_writes_the_backend_path_by_hand():
    """The literal is what drifted. A grep, because a passing sign-in test on a
    correct literal proves nothing about the next module that writes one."""
    from pathlib import Path

    services = Path(__file__).resolve().parents[1] / "services"
    for module in sorted(services.glob("*.py")):
        source = module.read_text()
        assert "backends.CaseInsensitiveEmailBackend" not in source, (
            f"{module.name} writes the backend path by hand; import BACKEND_PATH instead"
        )
