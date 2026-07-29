"""Back-office forms.

Staff-facing, so the tone is different from the public side: these can be terse
and assume domain knowledge. What they must not do is make a destructive or
compliance-relevant action easy to take by accident — hence the mandatory
reasons, and the explicit confirmation on the provisional-DBS grant.
"""

from __future__ import annotations

from django import forms

from directory.models import ConcernReport, VerificationStatus, VerificationType
from directory.services.verification import EXPIRING_TYPES


class InviteForm(forms.Form):
    email = forms.EmailField(
        label="Email address",
        help_text="They'll get a link to set up their account and start a draft listing.",
    )
    note = forms.CharField(
        label="Note (internal)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="Not shown to the practitioner. For your own records.",
    )


class ReviewDecisionForm(forms.Form):
    """Approve / request changes / reject, with notes.

    Notes are validated per-decision rather than being globally required:
    approving something clean should not need a paragraph, but sending it back
    without saying why leaves the practitioner guessing.
    """

    APPROVE = "approve"
    REQUEST_CHANGES = "request_changes"
    REJECT = "reject"

    decision = forms.ChoiceField(
        choices=[
            (APPROVE, "Approve and publish"),
            (REQUEST_CHANGES, "Request changes"),
            (REJECT, "Reject"),
        ],
        widget=forms.RadioSelect,
    )
    notes = forms.CharField(
        label="Notes",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Shown to the practitioner for “request changes”. Always recorded.",
    )

    def clean(self):
        data = super().clean()
        decision = data.get("decision")
        notes = (data.get("notes") or "").strip()

        if decision in {self.REQUEST_CHANGES, self.REJECT} and not notes:
            self.add_error(
                "notes",
                "Say what needs changing — the practitioner sees this, and it is the record.",
            )
        return data


class VerificationCheckForm(forms.Form):
    """One row of the workbench.

    The expiry rule is enforced here as well as in the service. The service is
    the guarantee; this is so a verifier gets a field error rather than a 500.
    """

    check_type = forms.ChoiceField(choices=VerificationType.choices, widget=forms.HiddenInput)
    status = forms.ChoiceField(choices=VerificationStatus.choices)
    expires_at = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="Required for insurance, DBS and ICO registration.",
    )
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        check_type = data.get("check_type")
        status = data.get("status")

        if (
            status == VerificationStatus.VERIFIED
            and check_type in EXPIRING_TYPES
            and not data.get("expires_at")
        ):
            self.add_error(
                "expires_at",
                "This type expires, so it needs an expiry date. Without one the badge "
                "could never lapse on its own.",
            )
        return data


class ProvisionalDBSForm(forms.Form):
    """Granting a provisional window.

    The confirmation checkbox is not decoration. Granting this publishes a
    listing for someone whose DBS has not come back, and the scope limit is the
    entire point — so the person clicking has to affirm they have read it.
    """

    confirm = forms.BooleanField(
        required=True,
        label=(
            "I understand this publishes the listing for adult work only. Under-18 client "
            "groups stay hidden until the DBS is verified."
        ),
        error_messages={"required": "Confirm the scope limit before granting."},
    )
    note = forms.CharField(
        label="Note",
        required=False,
        widget=forms.Textarea(attrs={"rows": 2}),
        help_text="e.g. “DBS submitted 3 July, reference 00012345”.",
    )


class SuspendForm(forms.Form):
    reason = forms.CharField(
        label="Reason",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=(
            "Recorded in the audit log and shown to staff only. The public page says "
            "nothing beyond “not currently listed”."
        ),
    )


class ConcernResolutionForm(forms.Form):
    outcome = forms.CharField(
        label="Outcome",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="What was actually done. This is the record that the concern was handled.",
    )


class AuditFilterForm(forms.Form):
    entity_type = forms.CharField(required=False)
    entity_id = forms.CharField(required=False)
    action = forms.CharField(required=False)
    actor = forms.CharField(required=False, help_text="Email address, or part of one.")
    date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


class ConcernFilterForm(forms.Form):
    category = forms.ChoiceField(
        required=False, choices=[("", "All categories"), *ConcernReport.Category.choices]
    )
    show_resolved = forms.BooleanField(required=False, label="Include resolved")
