"""
Every role check in this project lives here.

docs/multi-project-architecture.md §3: "keep role checks in a single access.py per
project". The reason is the OIDC migration — when the main site becomes the identity
provider, the mapping from claims to permissions changes in exactly one file.

Rule: views and templates call these predicates. They never inspect `user.role`
directly, and never compare user ids across projects.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import User

STAFF_ROLES = {User.Role.ADMIN, User.Role.VERIFIER, User.Role.SUPERADMIN}


def is_staff_role(user) -> bool:
    return bool(user and user.is_authenticated and user.role in STAFF_ROLES)


def can_review_submissions(user) -> bool:
    """Approve, request changes, publish, suspend."""
    return is_staff_role(user)


def can_view_evidence(user) -> bool:
    """
    Read a private verification document (ID, DBS, insurance certificate).

    Deliberately narrower than can_review_submissions: a reviewer can approve profile
    copy without opening someone's passport scan. Every call is logged by
    directory.services.documents.open_evidence().
    """
    return bool(user and user.is_authenticated and user.role in {User.Role.VERIFIER, User.Role.SUPERADMIN})


def can_grant_provisional_dbs(user) -> bool:
    """
    Start the clock on a listing that goes live for adult work while a DBS is pending.
    Restricted to VERIFIER and above, always audit-logged with the named actor.
    """
    return can_view_evidence(user)


def can_extend_provisional_window(user) -> bool:
    """
    An extension is a deliberate act, not a default. Second and subsequent extensions
    additionally require Dr. Abbass sign-off recorded on the practitioner — see
    docs/architecture.md, open decision 8.
    """
    return can_view_evidence(user)


def can_manage_taxonomy(user) -> bool:
    return bool(user and user.is_authenticated and user.role in {User.Role.ADMIN, User.Role.SUPERADMIN})


def owns_practitioner(user, practitioner) -> bool:
    return bool(user and user.is_authenticated and practitioner.user_id == user.id)


def can_edit_practitioner(user, practitioner) -> bool:
    return owns_practitioner(user, practitioner) or is_staff_role(user)


def require(predicate):
    """
    View decorator.

        @require(can_review_submissions)
        def review_queue(request): ...
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not predicate(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


# ===========================================================================
# Phase 0 additions — two-factor authentication
# ===========================================================================
# Everything above is the authored file and is left exactly as written. The
# functions below were added in Phase 0 to satisfy "TOTP 2FA enforced for admin,
# verifier and superadmin roles".
#
# They are deliberately kept SEPARATE from the capability predicates rather than
# folded into them, and that split is the design:
#
#   * a capability predicate answers "does this ROLE have this permission" — a
#     property of the account, and the thing the OIDC migration will re-map;
#   * two-factor answers "is this SESSION fully authenticated" — a property of
#     the request, and nothing to do with what the role may do.
#
# Mixing them would mean every predicate above had to re-check the session, and
# a future predicate that forgot would be a silent hole. Instead
# `accounts.middleware.TwoFactorEnforcementMiddleware` refuses to let an
# unverified staff session reach ANY page except the challenge, so a capability
# predicate is only ever evaluated on a session that has already cleared 2FA.
# That is a whitelist, not a checklist, and it fails closed.

#: Roles that must carry a confirmed TOTP device. Read by the middleware and the
#: enrolment views; do not test role strings for this anywhere else.
TWO_FACTOR_REQUIRED_ROLES = STAFF_ROLES


def requires_two_factor(user) -> bool:
    """Whether this account must carry a confirmed TOTP device at all."""
    return bool(user and user.is_authenticated and user.role in TWO_FACTOR_REQUIRED_ROLES)


def has_verified_two_factor(user) -> bool:
    """Whether *this session* has been verified against a TOTP device.

    ``django_otp``'s middleware sets ``user.otp_device`` when the session carries
    a verified device. Absent the middleware the attribute is missing, and the
    honest answer to "has this session been verified" is then no.
    """
    if not (user and user.is_authenticated):
        return False
    return getattr(user, "otp_device", None) is not None


def two_factor_satisfied(user) -> bool:
    """The gate itself: either 2FA is not required, or it has been completed."""
    if not (user and user.is_authenticated):
        return False
    return not requires_two_factor(user) or has_verified_two_factor(user)


# ===========================================================================
# Phase 2 additions — back-office capabilities
# ===========================================================================
# Named predicates for the two back-office actions the authored file did not
# have one for. Both currently resolve to the same role set as
# can_review_submissions, and that is deliberate rather than lazy:
#
#   * the authored can_review_submissions docstring already scopes it as
#     "Approve, request changes, publish, suspend" — so suspension is not a new
#     capability, it just had no name;
#   * docs/architecture.md's roles matrix lists "Invite" alongside review for
#     `admin`.
#
# They exist as separate names so a view reads as what it does, and so the two
# can diverge later (restricting suspension to verifier+, say) by editing one
# function rather than grepping for call sites — which is the entire reason
# CLAUDE.md puts every role check in this file.


def can_invite_users(user) -> bool:
    """Issue an invitation. Account creation is invite-only; this is the gate."""
    return can_review_submissions(user)


def can_suspend_listing(user) -> bool:
    """Pull a published listing, or restore one.

    Kept distinct from can_review_submissions despite resolving the same today:
    suspension is an enforcement action against a named professional, and if it
    is ever narrowed it should be narrowed here.
    """
    return can_review_submissions(user)
