"""Every role predicate in the project. There are no others.

Views and templates never inspect ``user.role``. They ask a question here. Two
reasons, both load-bearing:

1. The later move to central SSO (docs/multi-project-architecture.md §3, Option B)
   becomes a one-file change instead of a grep.
2. A permission rule that exists in one place can be *tested* in one place. The
   admin/verifier split below is a real control — ``admin`` deliberately cannot
   open somebody's passport scan — and it only holds if there is exactly one
   implementation of it.

Naming: ``can_*`` for capability questions, ``is_*`` for identity questions. The
decorators at the bottom are the only sanctioned way to gate a view.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from .models import TWO_FACTOR_REQUIRED_ROLES, Role

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def _role(user) -> str | None:
    if user is None or not user.is_authenticated or not user.is_active:
        return None
    return user.role


def is_practitioner(user) -> bool:
    return _role(user) == Role.PRACTITIONER


def is_admin(user) -> bool:
    """Strictly the admin role — NOT "admin or above".

    Use ``can_*`` for capability questions. This one is for the rare case that
    genuinely means "this person is an admin and not a verifier".
    """
    return _role(user) == Role.ADMIN


def is_verifier(user) -> bool:
    return _role(user) == Role.VERIFIER


def is_superadmin(user) -> bool:
    return _role(user) == Role.SUPERADMIN


def is_staff_role(user) -> bool:
    """Anyone who works for Kiam, as opposed to a listed practitioner."""
    return _role(user) in {Role.ADMIN, Role.VERIFIER, Role.SUPERADMIN}


# ---------------------------------------------------------------------------
# Two-factor
# ---------------------------------------------------------------------------


def requires_two_factor(user) -> bool:
    """Whether this account must carry a confirmed TOTP device at all."""
    return _role(user) in TWO_FACTOR_REQUIRED_ROLES


def has_verified_two_factor(user) -> bool:
    """Whether *this session* has been verified against a TOTP device.

    ``django_otp``'s middleware sets ``user.otp_device`` when the session carries a
    verified device. Absent the middleware the attribute is missing, and the
    honest answer to "has this session been verified" is then no.
    """
    if _role(user) is None:
        return False
    return getattr(user, "otp_device", None) is not None


def two_factor_satisfied(user) -> bool:
    """The gate itself: either 2FA is not required, or it has been completed."""
    if _role(user) is None:
        return False
    return not requires_two_factor(user) or has_verified_two_factor(user)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
# Every capability below is a question a view or template asks. Add to this list
# rather than testing a role anywhere else.


def can_access_dashboard(user) -> bool:
    """The practitioner self-service area (Phase 6)."""
    return is_practitioner(user) and two_factor_satisfied(user)


def can_access_backoffice(user) -> bool:
    """The staff queues (Phase 2)."""
    return is_staff_role(user) and two_factor_satisfied(user)


def can_review_submissions(user) -> bool:
    """Approve, request changes on, or publish a submitted profile."""
    return can_access_backoffice(user)


def can_manage_taxonomy(user) -> bool:
    return can_access_backoffice(user)


def can_invite_users(user) -> bool:
    """Account creation is by invite only — there is no public signup route."""
    return can_access_backoffice(user)


def can_suspend_listing(user) -> bool:
    return can_access_backoffice(user)


def can_view_private_evidence(user) -> bool:
    """Open a verification document (passport, DBS certificate, registration).

    ``admin`` is excluded on purpose, and this is the single reason the verifier
    role exists: approving profile copy and inspecting identity documents are
    different jobs, and every access is logged (docs/architecture.md, "Roles").
    Widening this predicate widens who can read scans of people's passports.
    """
    return _role(user) in {Role.VERIFIER, Role.SUPERADMIN} and two_factor_satisfied(user)


def can_grant_provisional_dbs(user) -> bool:
    """Let a practitioner go live for adult work with a DBS still pending.

    Not a verification toggle. ``Practitioner.is_verified`` and
    ``minor_work_status`` stay computed from dated ``VerificationCheck`` rows by
    ``directory.services.verification.recompute()`` — there is no admin toggle and
    none may be added (CLAUDE.md). This grants the *input*, and writes an audit
    entry; the state is still derived.
    """
    return can_view_private_evidence(user)


def can_use_django_admin(user) -> bool:
    return is_superadmin(user) and two_factor_satisfied(user)


# ---------------------------------------------------------------------------
# View decorators
# ---------------------------------------------------------------------------


def require(predicate):
    """Gate a view on one of the predicates above.

        @require(can_view_private_evidence)
        def evidence_view(request, ...): ...

    An anonymous user is sent to log in; a signed-in user who simply lacks the
    capability gets a 403. Redirecting the second case to a login page would tell
    them the URL exists and invite them to try another account.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if predicate(request.user):
                return view(request, *args, **kwargs)
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            raise PermissionDenied

        return wrapper

    return decorator
