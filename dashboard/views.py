"""The practitioner's dashboard. Thin — the work is in ``dashboard/services/``.

**Access is two predicates, never a role string.** ``can_use_dashboard`` says this
kind of account has a dashboard at all; ``owns_practitioner`` says this is their
listing. Both come from ``accounts.access``, and no view here reads ``user.role``
(CLAUDE.md). The pairing matters: the first alone would let a staff account edit
somebody's listing through a UI with no review path, and the second alone would
depend on every view remembering to check.

**Nothing in this app writes a verification field, a publication status or a
completeness score directly.** Those belong to
``directory.services.verification``, ``backoffice.services.publication`` and
``directory.services.completeness`` respectively, and a practitioner advancing
their own verification state by any route is the one thing CLAUDE.md forbids
outright. Every state change here goes through the service that owns it.

**Every page is ``noindex`` and ``never_cache``.** It is behind a login, but a
dashboard is somebody's own personal data and neither a robots directive nor a
shared proxy should be the only thing keeping it private.
"""

from __future__ import annotations

import logging
from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from accounts.access import (
    can_disable_two_factor,
    can_use_dashboard,
    owns_practitioner,
)
from accounts.services import email_change, ratelimit, two_factor
from accounts.services import sessions as sessions_service
from backoffice.services import publication, review
from directory.models import (
    AuditLog,
    Document,
    PractitionerLocation,
    PublicationStatus,
    TaxonomyRequest,
    VerificationType,
)
from directory.services import completeness
from directory.services import documents as evidence

from . import forms
from .services import editing, insights, overview

logger = logging.getLogger("dashboard")


def practitioner_view(view):
    """Resolve the signed-in practitioner's own listing, or 404.

    A 404 rather than a 403 for an account with no listing: "you may not see this"
    tells somebody there is something there. A staff account gets the same 404 —
    the back office is where staff act on a listing, with an audit trail and a
    review path, and this UI has neither.
    """

    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not can_use_dashboard(request.user):
            raise Http404

        practitioner = getattr(request.user, "practitioner", None)
        if practitioner is None:
            raise Http404

        if not owns_practitioner(request.user, practitioner):  # pragma: no cover — belt and braces
            raise PermissionDenied

        return view(request, practitioner, *args, **kwargs)

    return wrapped


def _context(practitioner, **extra) -> dict:
    """What every dashboard page needs for its own chrome."""
    return {
        "practitioner": practitioner,
        "status": overview.status(practitioner),
        "completeness": completeness.report(practitioner),
        "controlled_consequence": editing.CONTROLLED_CONSEQUENCE,
        "controlled_consequence_short": editing.CONTROLLED_CONSEQUENCE_SHORT,
        "safe_consequence": editing.SAFE_CONSEQUENCE,
        **extra,
    }


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@practitioner_view
def home(request, practitioner):
    return render(
        request,
        "dashboard/overview.html",
        _context(
            practitioner,
            **overview.build(practitioner),
            insights=insights.snapshot(practitioner),
        ),
    )


@practitioner_view
@require_http_methods(["POST"])
def submit(request, practitioner):
    """Send a draft to us to check.

    The one place a practitioner changes their own publication status, and it only
    goes one way: DRAFT/CHANGES_REQUESTED → SUBMITTED. ``review.submit()`` runs the
    lint first and refuses on a BLOCK, leaving them in DRAFT to fix it.
    """
    if practitioner.status not in {PublicationStatus.DRAFT, PublicationStatus.CHANGES_REQUESTED}:
        messages.info(request, "That listing is not waiting to be sent to us.")
        return redirect("dashboard:home")

    report = completeness.report(practitioner)
    if report.missing:
        messages.error(
            request,
            "There are still some things to fill in before we can check your listing — "
            f"starting with: {report.next_action.text.lower()}.",
        )
        return redirect("dashboard:home")

    try:
        review.submit(practitioner, actor=request.user)
    except review.SubmissionBlocked as blocked:
        for finding in blocked.result.blocks:
            messages.error(request, finding.message)
        return redirect("dashboard:home")

    messages.success(
        request,
        "Sent. We will check your details and your evidence, and email you when it is done.",
    )
    return redirect("dashboard:home")


# ---------------------------------------------------------------------------
# Editors
# ---------------------------------------------------------------------------


def _editor(request, practitioner, *, form_class, template, redirect_to):
    """The shared save path for the three plain ModelForm editors.

    One function so that "what happens when a controlled field changes" is decided
    once. An editor that forgot to call ``editing.save()`` would publish a name
    change with the badge still on it.
    """
    form = form_class(request.POST or None, request.FILES or None, instance=practitioner)

    if request.method == "POST" and form.is_valid():
        try:
            outcome = editing.save(form, practitioner=practitioner, actor=request.user)
        except editing.PublicationBlocked as blocked:
            # Nothing was saved — the gate raises inside the transaction.
            for reason in blocked.reasons:
                form.add_error(None, reason)
        else:
            level, text = editing.message_for(outcome, form)
            getattr(messages, level)(request, text)

            for finding in getattr(form, "held_findings", []):
                messages.warning(request, finding.message)

            return redirect(redirect_to)

    return render(
        request,
        template,
        _context(practitioner, form=form),
    )


@practitioner_view
@require_http_methods(["GET", "POST"])
def profile(request, practitioner):
    return _editor(
        request,
        practitioner,
        form_class=forms.ProfileForm,
        template="dashboard/profile.html",
        redirect_to="dashboard:profile",
    )


@practitioner_view
@require_http_methods(["GET", "POST"])
def availability(request, practitioner):
    return _editor(
        request,
        practitioner,
        form_class=forms.AvailabilityForm,
        template="dashboard/availability.html",
        redirect_to="dashboard:availability",
    )


@practitioner_view
@require_http_methods(["GET", "POST"])
def taxonomy(request, practitioner):
    """Specialities, approaches, client groups, languages, funding, formats.

    ``client_groups`` is controlled, so this editor can withdraw a badge — and
    adding an under-18 group is what makes an enhanced DBS required at all, which
    ``verification.recompute()`` notices through ``submit_update``.
    """
    form = forms.TaxonomyForm(request.POST or None, instance=practitioner)

    if request.method == "POST" and form.is_valid():
        try:
            outcome = editing.save(form, practitioner=practitioner, actor=request.user)
        except editing.PublicationBlocked as blocked:
            for reason in blocked.reasons:
                form.add_error(None, reason)
            return render(
                request,
                "dashboard/taxonomy.html",
                _context(
                    practitioner,
                    form=form,
                    pending_requests=practitioner.taxonomy_requests.filter(resolved_at__isnull=True),
                ),
            )

        requested = form.taxonomy_requests()
        for axis, term in requested:
            TaxonomyRequest.objects.get_or_create(
                practitioner=practitioner,
                axis=axis,
                proposed_term=term[:120],
                resolved_at=None,
            )

        level, text = editing.message_for(outcome, form)
        getattr(messages, level)(request, text)

        if requested:
            messages.info(
                request,
                "Thanks — we will look at adding "
                + ", ".join(f"“{term}”" for _axis, term in requested)
                + ". It will not appear on your listing until we do.",
            )
        return redirect("dashboard:taxonomy")

    return render(
        request,
        "dashboard/taxonomy.html",
        _context(
            practitioner,
            form=form,
            pending_requests=practitioner.taxonomy_requests.filter(resolved_at__isnull=True),
        ),
    )


@practitioner_view
@require_http_methods(["GET", "POST"])
def credentials(request, practitioner):
    """Qualifications, registrations and prescribing status — all controlled.

    Two formsets alongside the form, and their changes are passed to
    ``editing.save()`` explicitly. ``registrations`` and ``qualifications`` are
    separate models, so nothing about them appears in ``form.changed_data``, and a
    caller that forgot to pass them would let somebody change their GMC number
    with the badge still on the listing.
    """
    form = forms.CredentialsForm(request.POST or None, instance=practitioner)
    qualifications = forms.QualificationFormSet(
        request.POST or None, instance=practitioner, prefix="qualifications"
    )
    registrations = forms.RegistrationFormSet(
        request.POST or None, instance=practitioner, prefix="registrations"
    )

    if (
        request.method == "POST"
        and form.is_valid()
        and qualifications.is_valid()
        and registrations.is_valid()
    ):
        related_changed = []
        if qualifications.has_changed():
            related_changed.append("qualifications")
        if registrations.has_changed():
            related_changed.append("registrations")

        try:
            # The formsets are saved INSIDE `editing.save()`'s transaction. They
            # used to be saved here first, which meant a deleted registration
            # committed even when the rest of the edit failed — and it is the
            # deletion that can strand a restricted title with nothing behind it.
            outcome = editing.save(
                form,
                practitioner=practitioner,
                actor=request.user,
                related_changed=related_changed,
                formsets=(qualifications, registrations),
            )
        except editing.PublicationBlocked as blocked:
            for reason in blocked.reasons:
                form.add_error(None, reason)
        else:
            level, text = editing.message_for(outcome, form)
            getattr(messages, level)(request, text)
            return redirect("dashboard:credentials")

    return render(
        request,
        "dashboard/credentials.html",
        _context(
            practitioner,
            form=form,
            qualifications=qualifications,
            registrations=registrations,
        ),
    )


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


@practitioner_view
def locations(request, practitioner):
    return render(
        request,
        "dashboard/locations.html",
        _context(practitioner, locations=practitioner.locations.all()),
    )


@practitioner_view
@require_http_methods(["GET", "POST"])
def location_edit(request, practitioner, pk=None):
    """Add or edit one address. Geocoded by the form; see ``LocationForm.clean``."""
    instance = get_object_or_404(PractitionerLocation, pk=pk, practitioner=practitioner) if pk else None
    form = forms.LocationForm(request.POST or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        location = form.save(commit=False)
        location.practitioner = practitioner
        location.save()
        completeness.recompute(practitioner)
        publication.bust_cache(practitioner)

        messages.success(
            request,
            "Saved. People searching near there will find you."
            if location.is_public
            else "Saved. We will match you to searches in that area, and the address stays private.",
        )
        return redirect("dashboard:locations")

    return render(
        request,
        "dashboard/location_edit.html",
        _context(practitioner, form=form, location=instance),
    )


@practitioner_view
@require_http_methods(["POST"])
def location_delete(request, practitioner, pk):
    location = get_object_or_404(PractitionerLocation, pk=pk, practitioner=practitioner)
    location.delete()
    completeness.recompute(practitioner)
    publication.bust_cache(practitioner)
    messages.success(request, "That address has been removed from your listing.")
    return redirect("dashboard:locations")


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


@practitioner_view
@require_http_methods(["GET", "POST"])
def documents(request, practitioner):
    """Upload evidence. Straight to private storage, scanned first.

    There is deliberately no way to *view* an uploaded document from here. The
    only door to a private object is ``documents.open_evidence()``, which requires
    ``can_view_evidence`` and writes an access log row — a practitioner
    re-downloading their own passport scan through a second, unlogged route would
    put a public URL on the evidence bucket, which is the one thing
    ``directory/storages.py`` exists to make impossible. They can see that a file
    is there, what it is, and what state it is in.
    """
    # The choices come from the practitioner's OWN checklist, not from a fixed
    # list. The fixed list omitted DBS, which is the one the overview's
    # provisional countdown tells them to upload and links them here to do —
    # so a PROVISIONAL practitioner watching a 56-day window could not do the
    # thing the window demands, and it would lapse to BLOCKED. Found at the
    # Phase 6 compliance review.
    form = forms.EvidenceUploadForm(
        request.POST or None,
        request.FILES or None,
        choices=_upload_choices(practitioner),
    )

    if request.method == "POST" and form.is_valid():
        if _upload_rate_limited(request):
            messages.error(
                request,
                "That is a lot of uploads in a short time. Please wait a few minutes and try again.",
            )
            return redirect("dashboard:documents")

        try:
            document = evidence.upload(
                practitioner,
                check_type=form.cleaned_data["check_type"],
                upload_file=form.cleaned_data["file"],
                actor=request.user,
            )
        except evidence.UploadRejected as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Uploaded “{document.original_filename}”. We will check it and let you know.",
            )
        return redirect("dashboard:documents")

    return render(
        request,
        "dashboard/documents.html",
        _context(
            practitioner,
            form=form,
            checks=overview.checks(practitioner),
            documents=practitioner.documents.select_related("verification_check").order_by("-uploaded_at"),
            type_labels=dict(VerificationType.choices),
        ),
    )


@practitioner_view
@require_http_methods(["POST"])
def document_delete(request, practitioner, pk):
    """Withdraw a document — but never one behind a verified check.

    The brief: "can replace but not delete a verified document". A badge asserting
    that somebody's registration was checked, with the checked thing deleted at
    their request, leaves Kiam asserting something it can no longer show. Retention
    past that point is ``Document.delete_after`` and a solicitor's question.
    """
    document = get_object_or_404(Document, pk=pk, practitioner=practitioner)

    if not evidence.can_delete(document):
        messages.error(
            request,
            "We have already checked that document, so it stays on file as the evidence "
            "behind your verification. You can upload a newer one to replace it.",
        )
        return redirect("dashboard:documents")

    AuditLog.objects.create(
        actor=request.user,
        action="evidence.deleted",
        entity_type="Document",
        entity_id=str(document.pk),
        before={
            "practitioner_id": str(practitioner.pk),
            "type": document.type,
            "filename": document.original_filename,
            "sha256": document.sha256,
        },
    )
    document.file.delete(save=False)
    document.delete()
    messages.success(request, "That document has been deleted.")
    return redirect("dashboard:documents")


def _upload_choices(practitioner):
    """Every check this listing needs, in the order the overview lists them.

    Derived from `overview.checks()` so the upload form and the "what we still
    need" panel cannot disagree about what is outstanding, intersected with
    `documents.UPLOADABLE_TYPES` so it can never offer something the service will
    refuse.
    """
    labels = dict(VerificationType.choices)
    wanted = [c.type for c in overview.checks(practitioner) if c.type in evidence.UPLOADABLE_TYPES]
    # Plus anything they may legitimately send that is not currently outstanding —
    # a replacement for an in-date certificate, say.
    for check_type in evidence.UPLOADABLE_TYPES:
        if check_type not in wanted:
            wanted.append(check_type)
    return [(value, labels[value]) for value in wanted]


def _upload_rate_limited(request) -> bool:
    ip = ratelimit.client_ip(request)
    if not ip:
        return False
    return ratelimit.hit("evidence_upload", ip, limit=20, window_seconds=3600).exceeded


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


@practitioner_view
def insights_view(request, practitioner):
    return render(
        request,
        "dashboard/insights.html",
        _context(
            practitioner,
            report=insights.build(practitioner),
            caveats=insights.CAVEATS,
        ),
    )


# ---------------------------------------------------------------------------
# Account and security
# ---------------------------------------------------------------------------


@practitioner_view
@require_http_methods(["GET", "POST"])
def security(request, practitioner):
    form = forms.EmailChangeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            email_change.start(
                request.user,
                form.cleaned_data["new_email"],
                ip=ratelimit.client_ip(request),
            )
        except email_change.EmailChangeError as exc:
            form.add_error("new_email", str(exc))
        else:
            messages.success(
                request,
                "Check both inboxes. We have emailed your current address and the new one, "
                "and the change happens once you have confirmed at both.",
            )
            return redirect("dashboard:security")

    return render(
        request,
        "dashboard/security.html",
        _context(
            practitioner,
            form=form,
            pending_email_change=email_change.open_request_for(request.user),
            sessions=sessions_service.for_user(request.user, current_key=request.session.session_key or ""),
            has_two_factor=two_factor.has_device(request.user),
            can_disable_two_factor=can_disable_two_factor(request.user),
            has_password=request.user.has_usable_password(),
        ),
    )


@practitioner_view
@require_http_methods(["POST"])
def cancel_email_change(request, practitioner):  # noqa: ARG001
    if email_change.cancel(request.user):
        messages.success(request, "That email change has been cancelled. Nothing has changed.")
    return redirect("dashboard:security")


@practitioner_view
@require_http_methods(["POST"])
def revoke_session(request, practitioner):  # noqa: ARG001
    key = request.POST.get("session_key", "")

    if key == request.session.session_key:
        messages.info(request, "That is the device you are using now — sign out instead.")
    elif sessions_service.revoke(request.user, key):
        messages.success(request, "That device has been signed out.")
    else:
        messages.info(request, "That device is not signed in any more.")

    return redirect("dashboard:security")


@practitioner_view
@require_http_methods(["POST"])
def revoke_other_sessions(request, practitioner):  # noqa: ARG001
    count = sessions_service.revoke_all_except(request.user, request.session.session_key or "")
    messages.success(
        request,
        f"Signed out of {count} other device{'' if count == 1 else 's'}."
        if count
        else "There were no other devices signed in.",
    )
    return redirect("dashboard:security")


@practitioner_view
@require_http_methods(["POST"])
def disable_two_factor(request, practitioner):  # noqa: ARG001
    if not can_disable_two_factor(request.user):
        raise PermissionDenied

    if two_factor.disable(request.user):
        messages.warning(
            request,
            "Two-factor authentication is off. You can turn it back on whenever you like.",
        )
    return redirect("dashboard:security")


# ---------------------------------------------------------------------------
# Taking the listing down
# ---------------------------------------------------------------------------


@practitioner_view
@require_http_methods(["GET", "POST"])
def unpublish(request, practitioner):
    """One clear action, a confirmation, immediate effect.

    Consent is the lawful basis for publishing this data, so withdrawing it has to
    be at least as easy as giving it. There is no "contact us to be removed"
    anywhere in this flow, and there must not be: that wording on the paper intake
    form is being changed to match this page.

    ``publication.withdraw_consent()`` does both halves in one transaction — the
    listing comes down AND ``ConsentRecord.withdrawn_at`` is written. Doing one
    without the other would leave the record saying somebody still consents to a
    listing that is gone, or a live listing with no consent behind it.
    """
    form = forms.ConfirmActionForm(request.POST or None, word="TAKE DOWN")

    if request.method == "POST" and form.is_valid():
        publication.withdraw_consent(
            practitioner,
            actor=request.user,
            reason=form.cleaned_data.get("reason", ""),
        )
        messages.success(
            request,
            "Your listing has been taken down. It is no longer public and will not appear "
            "in search. Everything you have written is kept, so you can ask us to put it "
            "back whenever you want.",
        )
        return redirect("dashboard:home")

    return render(
        request,
        "dashboard/unpublish.html",
        _context(practitioner, form=form),
    )


@practitioner_view
@require_http_methods(["GET", "POST"])
def remove(request, practitioner):
    """Full removal, and the deletion of the evidence behind it.

    Offered separately from unpublishing because they are different asks.
    Withdrawal is "take my page down"; removal is "and stop holding my documents".
    Collapsing them would mean either that somebody taking a break for a month
    loses their evidence, or that somebody who wants to be gone stays on file.
    """
    form = forms.ConfirmActionForm(request.POST or None, word="REMOVE")

    if request.method == "POST" and form.is_valid():
        publication.request_removal(
            practitioner,
            actor=request.user,
            reason=form.cleaned_data.get("reason", ""),
        )
        messages.success(
            request,
            "Your listing has been removed and your documents are scheduled for deletion. "
            "We have emailed you a copy of this for your records.",
        )
        return redirect("dashboard:home")

    return render(
        request,
        "dashboard/remove.html",
        _context(practitioner, form=form, documents=practitioner.documents.count()),
    )
