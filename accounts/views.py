"""Auth views. Thin — the flow lives in ``accounts/services/``.

There is no signup view and no signup URL. Account creation is by admin ``Invite``
only, which arrives in Phase 2 (docs/roadmap.md).

Every page here is ``noindex``: a login form has no business in a search index,
and a magic-link URL certainly does not.
"""

from __future__ import annotations

import io

from django.contrib import messages
from django.contrib.auth import logout
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .access import requires_two_factor
from .forms import MagicLinkRequestForm, TOTPCodeForm
from .services import magic_link, ratelimit, two_factor


def _post_login_redirect(user) -> str:
    if requires_two_factor(user):
        target = (
            "accounts:two_factor_setup" if two_factor.needs_enrolment(user) else "accounts:two_factor_verify"
        )
        return reverse(target)
    return reverse("pages:home")


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    """Request a magic link.

    The response is identical whether or not the address matched an account —
    including when the honeypot fires. Anything else here is an account oracle.
    """
    if request.user.is_authenticated:
        return redirect(_post_login_redirect(request.user))

    form = MagicLinkRequestForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        if not form.is_bot:
            try:
                magic_link.request_link(
                    form.cleaned_data["email"],
                    ip=ratelimit.client_ip(request),
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                )
            except magic_link.RateLimited:
                # The one honest failure. It is about the requester's rate, not
                # about whether the account exists, so it leaks nothing.
                form.add_error(
                    None,
                    "Too many sign-in requests. Please wait a little while before trying again.",
                )
                return render(request, "accounts/login.html", {"form": form}, status=429)

        return redirect("accounts:login_sent")

    return render(request, "accounts/login.html", {"form": form})


@never_cache
def login_sent_view(request):
    """ "Check your email" — shown for every request, matched or not."""
    return render(request, "accounts/login_sent.html")


@never_cache
@require_http_methods(["GET"])
def magic_link_consume_view(request, token: str):
    """Redeem a link and start the session."""
    user = magic_link.consume(token)

    if user is None:
        return render(request, "accounts/link_invalid.html", status=410)

    magic_link.log_in(request, user)
    return redirect(_post_login_redirect(user))


@never_cache
@require_http_methods(["POST"])
def logout_view(request):
    """POST only — a GET logout is a one-pixel-image CSRF away from being an
    annoyance, and the kiam-ui header already posts this with a CSRF token."""
    logout(request)
    return redirect("pages:home")


# ---------------------------------------------------------------------------
# Two-factor
# ---------------------------------------------------------------------------


@never_cache
@require_http_methods(["GET", "POST"])
def two_factor_setup_view(request):
    """Enrol a TOTP device. Reachable only by a role that requires one."""
    if not request.user.is_authenticated or not requires_two_factor(request.user):
        raise Http404

    if two_factor.has_device(request.user):
        return redirect("accounts:two_factor_verify")

    device = two_factor.start_enrolment(request.user)
    form = TOTPCodeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        # confirm_enrolment marks the session verified itself — re-presenting the
        # same code would be refused as a replay. See the note in the service.
        if two_factor.confirm_enrolment(request, request.user, device, form.cleaned_data["code"]):
            messages.success(request, "Two-factor authentication is now set up on your account.")
            return redirect("pages:home")
        form.add_error("code", "That code wasn't right. Check your authenticator app and try again.")

    return render(
        request,
        "accounts/two_factor_setup.html",
        {
            "form": form,
            # The secret is shown as text as well as a QR code: a QR code is
            # unusable to anyone who cannot see it, or who is enrolling on the
            # same device that is displaying it (WCAG 1.1.1).
            "secret": device.key,
            "issuer": two_factor.issuer(),
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def two_factor_verify_view(request):
    """Answer the TOTP challenge for this session."""
    if not request.user.is_authenticated or not requires_two_factor(request.user):
        raise Http404

    if two_factor.needs_enrolment(request.user):
        return redirect("accounts:two_factor_setup")

    form = TOTPCodeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        if two_factor.verify(request, request.user, form.cleaned_data["code"]):
            return redirect("pages:home")
        form.add_error("code", "That code wasn't right. Check your authenticator app and try again.")

    return render(request, "accounts/two_factor_verify.html", {"form": form})


@never_cache
def two_factor_qr_view(request):
    """The enrolment QR code as an SVG.

    Rendered here rather than linked to a third-party chart API — the image URL
    would carry the TOTP secret to someone else's server.
    """
    if not request.user.is_authenticated or not requires_two_factor(request.user):
        raise Http404

    device = two_factor.get_device(request.user) or two_factor.start_enrolment(request.user)
    if device.confirmed:
        raise Http404

    import qrcode
    import qrcode.image.svg

    image = qrcode.make(two_factor.provisioning_uri(device), image_factory=qrcode.image.svg.SvgPathImage)
    buffer = io.BytesIO()
    image.save(buffer)

    response = HttpResponse(buffer.getvalue(), content_type="image/svg+xml")
    response["Cache-Control"] = "no-store"
    return response
