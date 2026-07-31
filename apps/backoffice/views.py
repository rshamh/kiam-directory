"""Back-office views. Thin — the work is in ``backoffice/services/``.

Every view is gated by a predicate from ``accounts.access``. None of them tests
``user.role``, and the two-factor middleware from Phase 0 means an unverified
staff session never reaches any of them.

No page here is public and every one is ``noindex`` — the whole app is behind
the staff gate, but a misconfigured robots.txt should not be the only thing
standing between a review queue and a search index.
"""

from __future__ import annotations

import contextlib
import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.accounts.access import (
    can_grant_provisional_dbs,
    can_invite_users,
    can_review_submissions,
    can_suspend_listing,
    can_view_evidence,
    require,
)
from apps.accounts.models import Invite
from apps.accounts.services import magic_link, ratelimit
from apps.directory.models import (
    AuditLog,
    ConcernReport,
    Document,
    Practitioner,
    PublicationStatus,
    ReviewRequest,
    VerificationCheck,
    VerificationType,
)
from apps.directory.services import documents as evidence
from apps.directory.services import verification

from . import forms
from .services import concerns as concerns_service
from .services import invites as invites_service
from .services import publication, review

logger = logging.getLogger("backoffice")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@never_cache
@require(can_review_submissions)
def dashboard(request):
    return render(
        request,
        "backoffice/dashboard.html",
        {
            "review_count": review.open_queue().count(),
            "concern_count": concerns_service.open_queue().count(),
            "expiring_count": Practitioner.objects.filter(
                status=PublicationStatus.PUBLISHED, is_verified=False
            ).count(),
            "pending_invites": Invite.objects.filter(accepted_at__isnull=True).count(),
        },
    )


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


@never_cache
@require(can_invite_users)
@require_http_methods(["GET", "POST"])
def invite_list(request):
    form = forms.InviteForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            invite = invites_service.issue_and_send(
                form.cleaned_data["email"],
                invited_by=request.user,
                note=form.cleaned_data["note"],
            )
        except invites_service.InviteError as exc:
            form.add_error("email", str(exc))
        else:
            messages.success(request, f"Invite sent to {invite.email}.")
            return redirect("backoffice:invite_list")

    return render(
        request,
        "backoffice/invite_list.html",
        {
            "form": form,
            "invites": Invite.objects.select_related("invited_by").order_by("-created_at")[:100],
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def invite_accept(request, token: str):
    """Public only in the sense that the holder of a token reaches it.

    Not a signup route: it consumes an invite an admin issued. There is no way
    to reach an account from here without one.
    """
    invite = invites_service.lookup(token)
    if invite is None:
        # Expired, used and never-existed are one page on purpose.
        return render(request, "backoffice/invite_invalid.html", status=410)

    if request.method == "POST":
        user = invites_service.accept(token)
        if user is None:
            return render(request, "backoffice/invite_invalid.html", status=410)

        # They now sign in the same way everyone else does. No password is ever
        # set, and the invite token is spent.
        #
        # A rate-limited send is swallowed rather than surfaced: the invite has
        # already been consumed at this point, so failing loudly here would tell
        # them their account does not exist when it does. They can request
        # another link from the sign-in page.
        with contextlib.suppress(magic_link.RateLimited):
            magic_link.request_link(user.email, ip=ratelimit.client_ip(request))
        return redirect("accounts:login_sent")

    return render(request, "backoffice/invite_accept.html", {"invite": invite})


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------


@never_cache
@require(can_review_submissions)
def review_queue(request):
    return render(request, "backoffice/review_queue.html", {"requests": review.open_queue()})


@never_cache
@require(can_review_submissions)
@require_http_methods(["GET", "POST"])
def review_detail(request, pk):
    review_request = get_object_or_404(
        ReviewRequest.objects.select_related("practitioner", "practitioner__profession"), pk=pk
    )
    practitioner = review_request.practitioner
    form = forms.ReviewDecisionForm(request.POST or None)

    # A live listing with a pending edit is a different decision from a listing
    # waiting to go live: the page never came down, so approving it publishes
    # nothing. See review.approve_update().
    is_pending_update = practitioner.status == PublicationStatus.PUBLISHED

    if request.method == "POST" and form.is_valid():
        decision = form.cleaned_data["decision"]
        notes = form.cleaned_data["notes"]
        try:
            if decision == forms.ReviewDecisionForm.APPROVE and is_pending_update:
                review.approve_update(review_request, actor=request.user, notes=notes)
                if practitioner.is_verified:
                    messages.success(request, f"Edit approved. {practitioner.full_name} keeps their badge.")
                else:
                    messages.warning(
                        request,
                        f"Edit approved, but {practitioner.full_name} has no badge: the checks "
                        "behind what changed were reopened and still need verifying.",
                    )
            elif decision == forms.ReviewDecisionForm.APPROVE:
                review.approve(review_request, actor=request.user, notes=notes)
                messages.success(request, f"{practitioner.full_name} is now published.")
            elif decision == forms.ReviewDecisionForm.REQUEST_CHANGES:
                review.request_changes(review_request, actor=request.user, notes=notes)
                messages.success(request, "Sent back with your notes.")
            else:
                review.reject(review_request, actor=request.user, notes=notes)
                messages.success(request, "Listing rejected.")
        except review.NotReviewable as exc:
            form.add_error(None, str(exc))
        else:
            return redirect("backoffice:review_queue")

    if (
        request.method == "GET"
        and practitioner.status == PublicationStatus.SUBMITTED
        and review_request.outcome == ""
    ):
        review.claim(review_request, actor=request.user)

    return render(
        request,
        "backoffice/review_detail.html",
        {
            "review_request": review_request,
            "practitioner": practitioner,
            "snapshot": review_request.snapshot,
            "lint_flags": review_request.lint_flags or {},
            "blocking_reasons": review.blocking_publication_reasons(practitioner),
            "form": form,
            "is_pending_update": is_pending_update,
            "reopened_checks": verification.checks_invalidated_by(review_request.changed_fields),
        },
    )


# ---------------------------------------------------------------------------
# Verification workbench
# ---------------------------------------------------------------------------


@never_cache
@require(can_review_submissions)
def verification_workbench(request, pk):
    """One row per VerificationType, whether or not a check exists yet.

    Rows for types with no check are the point: a missing insurance check is
    exactly what a verifier needs to see, and only rendering existing rows would
    hide it.
    """
    practitioner = get_object_or_404(Practitioner, pk=pk)
    existing = {c.type: c for c in practitioner.verifications.all()}
    docs = {}
    for document in practitioner.documents.all():
        docs.setdefault(document.type, []).append(document)

    rows = []
    for value, label in VerificationType.choices:
        check = existing.get(value)
        rows.append(
            {
                "type": value,
                "label": label,
                "check": check,
                "documents": docs.get(value, []),
                "required": value in verification.required_types(practitioner),
                "expires": value in verification.EXPIRING_TYPES,
            }
        )

    outstanding = [
        row["type"]
        for row in rows
        if row["required"] and (row["check"] is None or row["check"].status != "verified")
    ]

    return render(
        request,
        "backoffice/verification_workbench.html",
        {
            "practitioner": practitioner,
            "rows": rows,
            "can_view_evidence": can_view_evidence(request.user),
            "can_grant_provisional": can_grant_provisional_dbs(request.user),
            "statuses": VerificationCheck._meta.get_field("status").choices,
            "extension_blocked": practitioner.provisional_extensions
            >= verification.MAX_SELF_SERVICE_EXTENSIONS,
            "verify_all_form": forms.VerifyAllRequiredForm(),
            "outstanding_required": outstanding,
        },
    )


@never_cache
@require(can_view_evidence)
@require_http_methods(["POST"])
def verification_verify_all(request, pk):
    """Record a verification decision against every required check at once.

    Gated on ``can_view_evidence``, not ``can_review_submissions``, for the same
    reason ``verification_set_status`` is: this grants a badge, and the role that
    decides evidence is satisfactory has to be the role permitted to look at it.
    An `admin` who cannot open a passport scan cannot assert that they checked one.

    Not a toggle. It writes the same dated, expiring, audit-logged checks a
    verifier would write one at a time, and the badge is still computed from them
    — so it still lapses on the insurance date entered here.
    """
    practitioner = get_object_or_404(Practitioner, pk=pk)
    form = forms.VerifyAllRequiredForm(request.POST)

    if not form.is_valid():
        for field, errors in form.errors.items():
            messages.error(request, f"{field}: {'; '.join(errors)}")
        return redirect("backoffice:verification_workbench", pk=pk)

    try:
        verification.verify_all_required(
            practitioner,
            actor=request.user,
            expires_at={VerificationType.INSURANCE: form.cleaned_data["insurance_expires_at"]},
            notes=form.cleaned_data["notes"],
        )
    except verification.NothingToVerify as exc:
        messages.info(request, str(exc))
    except verification.ExpiryRequired as exc:
        messages.error(request, str(exc))
    else:
        practitioner.refresh_from_db()
        if practitioner.is_verified:
            messages.success(
                request,
                f"{practitioner.full_name} is verified — credentials checked "
                f"{practitioner.credentials_checked_at:%-d %B %Y}, lapsing "
                f"{practitioner.verification_expires_at:%-d %B %Y}.",
            )
        else:
            messages.warning(
                request,
                "Checks recorded, but the badge is still off — something required is "
                "missing or out of date. The rows below show which.",
            )

    return redirect("backoffice:verification_workbench", pk=pk)


@never_cache
@require(can_view_evidence)
@require_http_methods(["POST"])
def verification_set_status(request, pk, check_type):
    """Record a verifier's decision on one check.

    Gated on ``can_view_evidence``, NOT ``can_review_submissions``. Marking a
    check VERIFIED runs recompute(), which writes minor_work_status — so an
    `admin` reaching this could publish somebody's under-18 client groups off
    the back of a DBS they are not permitted to open. The role that decides
    evidence is verified must be the role allowed to look at it.
    """
    practitioner = get_object_or_404(Practitioner, pk=pk)
    form = forms.VerificationCheckForm(
        {
            "check_type": check_type,
            "status": request.POST.get("status", ""),
            "expires_at": request.POST.get("expires_at") or None,
            "notes": request.POST.get("notes", ""),
        }
    )

    if not form.is_valid():
        for error in form.errors.values():
            messages.error(request, "; ".join(error))
        return redirect("backoffice:verification_workbench", pk=pk)

    check, _ = VerificationCheck.objects.get_or_create(practitioner=practitioner, type=check_type)
    try:
        verification.set_check_status(
            check,
            status=form.cleaned_data["status"],
            actor=request.user,
            expires_at=form.cleaned_data["expires_at"],
            notes=form.cleaned_data["notes"],
        )
    except verification.ExpiryRequired as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{check.get_type_display()} updated.")

    return redirect("backoffice:verification_workbench", pk=pk)


@never_cache
@require(can_view_evidence)
@require_http_methods(["POST"])
def evidence_open(request, document_id):
    """Mint a short-lived link to one evidence file, and log the access.

    ``can_view_evidence`` excludes ``admin`` deliberately — approving profile
    copy and opening someone's passport scan are different jobs.
    """
    document = get_object_or_404(Document.objects.select_related("practitioner"), pk=document_id)

    url = evidence.open_evidence(
        document,
        user=request.user,
        ip=ratelimit.client_ip(request),
        reason=request.POST.get("reason", "verification workbench"),
    )
    return redirect(url)


@never_cache
def evidence_stream(request, token: str):
    """Serve a private evidence file for a signed, unexpired link.

    Re-checks the capability rather than trusting the signature: the signature
    proves the link was minted legitimately, not that whoever is holding it now
    is still entitled to it.
    """
    if not can_view_evidence(request.user):
        raise PermissionDenied("This account cannot open verification evidence.")

    # `user=` binds the token to whoever it was minted for, so a link forwarded
    # to a second verifier inside its TTL is refused rather than silently
    # attributing their read to the first person in the access log.
    document = evidence.verify_stream_token(token, user=request.user)

    try:
        handle = document.file.open("rb")
    except FileNotFoundError as exc:
        raise Http404("Evidence file is missing from storage.") from exc

    response = FileResponse(handle, content_type=document.mime_type or "application/octet-stream")
    response["Content-Disposition"] = f'inline; filename="{document.original_filename}"'
    # Never cached anywhere — this is somebody's identity document.
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    return response


# ---------------------------------------------------------------------------
# Provisional DBS
# ---------------------------------------------------------------------------


@never_cache
@require(can_grant_provisional_dbs)
@require_http_methods(["GET", "POST"])
def provisional_grant(request, pk):
    practitioner = get_object_or_404(Practitioner, pk=pk)
    form = forms.ProvisionalDBSForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        verification.grant_provisional_dbs(practitioner, actor=request.user, note=form.cleaned_data["note"])
        messages.success(
            request,
            "Provisional DBS window granted. The listing is live for adult work only — "
            "under-18 client groups stay hidden until the DBS is verified.",
        )
        return redirect("backoffice:verification_workbench", pk=pk)

    return render(
        request,
        "backoffice/provisional_grant.html",
        {
            "practitioner": practitioner,
            "form": form,
            "window_days": verification.PROVISIONAL_WINDOW_DAYS,
        },
    )


@never_cache
@require(can_grant_provisional_dbs)
@require_http_methods(["GET", "POST"])
def provisional_extend(request, pk):
    practitioner = get_object_or_404(Practitioner, pk=pk)
    blocked = practitioner.provisional_extensions >= verification.MAX_SELF_SERVICE_EXTENSIONS
    form = forms.ProvisionalDBSForm(request.POST or None)

    if request.method == "POST" and not blocked and form.is_valid():
        try:
            verification.extend_provisional_dbs(
                practitioner, actor=request.user, note=form.cleaned_data["note"]
            )
        except (verification.ExtensionRequiresSignOff, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Provisional window extended.")
            return redirect("backoffice:verification_workbench", pk=pk)

    return render(
        request,
        "backoffice/provisional_extend.html",
        {
            "practitioner": practitioner,
            "form": form,
            "blocked": blocked,
            "window_days": verification.PROVISIONAL_WINDOW_DAYS,
        },
    )


# ---------------------------------------------------------------------------
# Suspend / unpublish
# ---------------------------------------------------------------------------


@never_cache
@require(can_suspend_listing)
@require_http_methods(["GET", "POST"])
def suspend(request, pk):
    practitioner = get_object_or_404(Practitioner, pk=pk)
    form = forms.SuspendForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            publication.suspend(practitioner, actor=request.user, reason=form.cleaned_data["reason"])
        except publication.SuspensionError as exc:
            form.add_error("reason", str(exc))
        else:
            messages.success(request, f"{practitioner.full_name} is no longer listed.")
            return redirect("backoffice:practitioner_detail", pk=pk)

    return render(request, "backoffice/suspend.html", {"practitioner": practitioner, "form": form})


@never_cache
@require(can_suspend_listing)
@require_http_methods(["POST"])
def lift_suspension(request, pk):
    practitioner = get_object_or_404(Practitioner, pk=pk)
    try:
        publication.lift_suspension(practitioner, actor=request.user)
    except publication.SuspensionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{practitioner.full_name} is listed again.")
    return redirect("backoffice:practitioner_detail", pk=pk)


@never_cache
@require(can_review_submissions)
def practitioner_detail(request, pk):
    practitioner = get_object_or_404(Practitioner.objects.select_related("profession", "user"), pk=pk)
    return render(
        request,
        "backoffice/practitioner_detail.html",
        {
            "practitioner": practitioner,
            "reviews": practitioner.review_requests.order_by("-submitted_at")[:10],
            "can_suspend": can_suspend_listing(request.user),
            "blocking_reasons": review.blocking_publication_reasons(practitioner),
        },
    )


@never_cache
@require(can_review_submissions)
def practitioner_list(request):
    queryset = Practitioner.objects.select_related("profession").order_by("-updated_at")
    status = request.GET.get("status", "")
    if status:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "backoffice/practitioner_list.html",
        {
            "practitioners": queryset[:200],
            "statuses": PublicationStatus.choices,
            "selected_status": status,
        },
    )


# ---------------------------------------------------------------------------
# Concerns
# ---------------------------------------------------------------------------


@never_cache
@require(can_review_submissions)
def concern_queue(request):
    filter_form = forms.ConcernFilterForm(request.GET or None)
    queryset = concerns_service.open_queue()

    if filter_form.is_valid():
        if filter_form.cleaned_data.get("show_resolved"):
            queryset = ConcernReport.objects.select_related("practitioner", "handled_by").order_by(
                "-created_at"
            )
        if category := filter_form.cleaned_data.get("category"):
            queryset = queryset.filter(category=category)

    return render(
        request,
        "backoffice/concern_queue.html",
        {"concerns": queryset[:200], "filter_form": filter_form},
    )


@never_cache
@require(can_review_submissions)
@require_http_methods(["GET", "POST"])
def concern_detail(request, pk):
    concern = get_object_or_404(ConcernReport.objects.select_related("practitioner", "handled_by"), pk=pk)
    form = forms.ConcernResolutionForm(request.POST or None)

    if request.method == "POST":
        if request.POST.get("action") == "assign":
            concerns_service.assign(concern, actor=request.user)
            messages.success(request, "Assigned to you.")
            return redirect("backoffice:concern_detail", pk=pk)

        if form.is_valid():
            try:
                concerns_service.resolve(concern, actor=request.user, outcome=form.cleaned_data["outcome"])
            except concerns_service.ConcernError as exc:
                form.add_error("outcome", str(exc))
            else:
                messages.success(request, "Concern resolved.")
                return redirect("backoffice:concern_queue")

    return render(request, "backoffice/concern_detail.html", {"concern": concern, "form": form})


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


@never_cache
@require(can_review_submissions)
def audit_log(request):
    """Read-only. There is no delete view and no delete action, by construction.

    An audit log with a delete button is not an audit log — see the model admin,
    which also refuses add, change and delete.
    """
    filter_form = forms.AuditFilterForm(request.GET or None)
    queryset = AuditLog.objects.select_related("actor").order_by("-created_at")

    if filter_form.is_valid():
        data = filter_form.cleaned_data
        if data.get("entity_type"):
            queryset = queryset.filter(entity_type__iexact=data["entity_type"])
        if data.get("entity_id"):
            queryset = queryset.filter(entity_id=data["entity_id"])
        if data.get("action"):
            queryset = queryset.filter(action__icontains=data["action"])
        if data.get("actor"):
            queryset = queryset.filter(actor__email__icontains=data["actor"])
        if data.get("date_from"):
            queryset = queryset.filter(created_at__date__gte=data["date_from"])
        if data.get("date_to"):
            queryset = queryset.filter(created_at__date__lte=data["date_to"])

    return render(
        request,
        "backoffice/audit_log.html",
        {"entries": queryset[:500], "filter_form": filter_form},
    )
