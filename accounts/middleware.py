"""Enforce the second factor for staff roles.

``accounts.access`` already refuses every staff capability until
``two_factor_satisfied()`` is true, so this middleware is not what makes 2FA
mandatory — it is what makes it *usable*: without it, a staff member who has
authenticated by magic link but not yet answered the TOTP challenge would get a
403 from wherever they landed, with no route to the challenge page.

It fails closed. Anything that is not on the small exempt list is redirected to
enrolment or verification while the session is unverified, including the Django
admin.
"""

from __future__ import annotations

from django.shortcuts import redirect
from django.urls import reverse

from .access import has_verified_two_factor, requires_two_factor
from .services import two_factor

#: URL names reachable with an authenticated but not-yet-verified staff session.
#: Keep this list short and explicit: every name on it is a page a half-
#: authenticated staff member can reach.
EXEMPT_URL_NAMES = frozenset(
    {
        "accounts:login",
        "accounts:login_sent",
        "accounts:logout",
        "accounts:magic_link_consume",
        "accounts:two_factor_setup",
        # The enrolment QR image is part of the setup page. Without it here the
        # page renders and its <img> silently 302s to the page that contains it.
        "accounts:two_factor_qr",
        "accounts:two_factor_verify",
        "seo:healthz",
        # An invitee has no session yet — they reach this holding only a token.
        "backoffice:invite_accept",
    }
)

#: Path prefixes that must stay reachable regardless — static assets and the
#: public read surface, which needs no login at all.
EXEMPT_PATH_PREFIXES = ("/static/", "/media/", "/robots.txt", "/llms.txt", "/sitemap.xml")


class TwoFactorEnforcementMiddleware:
    """Redirect unverified staff sessions to enrolment or the TOTP challenge."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated:
            return self.get_response(request)
        if not requires_two_factor(user) or has_verified_two_factor(user):
            return self.get_response(request)
        if self._is_exempt(request):
            return self.get_response(request)

        target = (
            "accounts:two_factor_setup" if two_factor.needs_enrolment(user) else "accounts:two_factor_verify"
        )
        return redirect(reverse(target))

    @staticmethod
    def _is_exempt(request) -> bool:
        if request.path.startswith(EXEMPT_PATH_PREFIXES):
            return True

        match = getattr(request, "resolver_match", None)
        if match is None:
            # Middleware runs before URL resolution, so resolve it ourselves
            # rather than assume. Failing to resolve means a 404 is coming, and a
            # 404 is not somewhere to gate.
            from django.urls import Resolver404, resolve

            try:
                match = resolve(request.path_info)
            except Resolver404:
                return True

        return match.view_name in EXEMPT_URL_NAMES
