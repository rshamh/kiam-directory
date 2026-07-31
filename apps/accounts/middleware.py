"""Enforce the second factor: for staff always, for anyone who enrolled one.

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

import logging

from django.shortcuts import redirect
from django.urls import reverse

from .access import has_verified_two_factor, must_challenge_two_factor
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
        # `must_challenge_two_factor`, not `requires_two_factor`: Phase 6 lets a
        # practitioner enrol voluntarily, and a device they are never challenged
        # for is worse than no device, because they think they have one.
        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated:
            return self.get_response(request)
        if not must_challenge_two_factor(user) or has_verified_two_factor(user):
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


# ===========================================================================
# Phase 6 addition — knowing which devices are signed in
# ===========================================================================


class SessionActivityMiddleware:
    """Keep `UserSession` in step with the session this request arrived on.

    Three things keep this off the hot path:

    * **Anonymous requests do nothing.** One attribute check, and the public site
      — which is the overwhelming majority of traffic — never reaches the rest.
    * **Throttled through the cache, not the database.** `cache.add()` succeeds
      only once per key per window, so a signed-in practitioner clicking around
      writes one row every five minutes rather than one per request. A Redis
      `SETNX` per authenticated request is cheap; an `UPDATE` per request is not.
    * **It never fails a request.** A security page that is slightly out of date is
      a much smaller problem than a 500 on every page because the session table is
      briefly unavailable, so the write is best-effort and logged.

    It runs after `AuthenticationMiddleware` (it needs `request.user`) and after
    `SessionMiddleware` (it needs a `session_key`, which does not exist until the
    session has been saved at least once).
    """

    #: How often a given session is re-recorded. Five minutes is short enough that
    #: "last used" is honest on a security page and long enough that it is one
    #: write per browsing session rather than per click.
    TOUCH_SECONDS = 300

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        self._touch(request)
        return response

    def _touch(self, request) -> None:
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return

        session = getattr(request, "session", None)
        key = getattr(session, "session_key", None)
        if not key:
            return

        from django.core.cache import cache

        if not cache.add(f"session-seen:{key}", 1, timeout=self.TOUCH_SECONDS):
            return

        from .services import sessions

        try:
            sessions.record(request)
        except Exception:  # noqa: BLE001 — never fail a request over a security-page nicety
            logging.getLogger("accounts.sessions").exception("sessions.record_failed")
