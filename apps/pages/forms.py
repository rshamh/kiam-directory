"""Public forms.

One so far: reporting a concern about a listing.

It is deliberately not a ``ModelForm``. ``ConcernReport.practitioner`` is a
required foreign key, and a ``ModelForm`` would render it as a select of every
practitioner in the database — a public dropdown naming everyone listed,
including people whose listings are suspended. The listing is identified by the
link the reporter already has instead.
"""

from __future__ import annotations

from django import forms

from apps.backoffice.services.publication import is_publicly_visible
from apps.directory.models import ConcernReport, Practitioner

LISTING_NOT_FOUND = (
    "We couldn't find that listing. Paste the web address of the practitioner's "
    "page — it looks like directory.kiamclinic.com/p/their-name/ — or check the "
    "spelling of their name."
)


class ConcernForm(forms.Form):
    """Report a concern about what a listing says, or about who is on it.

    Not a complaints form about someone's care. The page says so at the top, in
    the field help below, and names the regulators that *do* take those — see
    templates/pages/report_concern.html.
    """

    # Every required field gets a message that names what is missing and what to
    # do. Django's "This field is required." names no field, gives no next step,
    # and reads as a system message — the register the brief rules out.
    listing = forms.CharField(
        label="Which listing is this about?",
        max_length=300,
        help_text="Paste the web address of their page, or type their name as it appears on it.",
        error_messages={
            "required": (
                "Tell us which listing this is about — paste the web address of "
                "their page, or type their name."
            )
        },
    )

    # A blank first option, so the form does not arrive with "Details are
    # inaccurate" already chosen and quietly collect that answer from anyone who
    # skipped the question. Both error messages are rewritten: Django's "Select a
    # valid choice" describes the validator's problem, not the reader's.
    category = forms.ChoiceField(
        label="What is the concern?",
        choices=[("", "Please choose…"), *ConcernReport.Category.choices],
        widget=forms.Select,
        error_messages={
            "required": "Please choose what the concern is about.",
            "invalid_choice": "Please choose what the concern is about.",
        },
    )

    detail = forms.CharField(
        label="What have you noticed?",
        widget=forms.Textarea(attrs={"rows": 6}),
        # "above", not "further down this page" — the regulator list is rendered
        # before the form. The template passes its own copy of this string today,
        # so the wrong one was invisible; it would have become wrong copy the
        # first time anyone rendered the field's own help text.
        help_text=(
            "Please include what you saw and where. If your concern is about the care "
            "you received, contact the practitioner's regulator instead — they are "
            "listed above."
        ),
        error_messages={"required": "Tell us what you noticed, so we know what to look at."},
    )

    reporter_email = forms.EmailField(
        label="Your email address (optional)",
        required=False,
        help_text=(
            "Only so we can come back to you if we need to. Leave it blank to report "
            "anonymously — we will still look into it."
        ),
        widget=forms.EmailInput(attrs={"autocomplete": "email", "spellcheck": "false"}),
    )

    # A honeypot, not a CAPTCHA — same reasoning as the sign-in form. A puzzle at
    # the door is a real barrier for this audience (golden rule #3); a field no
    # human sees costs them nothing.
    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off", "aria-hidden": "true"}),
        label="Leave this field empty",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.practitioner: Practitioner | None = None

    def clean_listing(self) -> str:
        raw = (self.cleaned_data["listing"] or "").strip()

        practitioner = self._by_slug(raw) or self._by_name(raw)

        # A suspended listing answers the same way as one that never existed —
        # the same rule the profile view follows, for the same reason. Somebody
        # probing this form must not be able to use it to confirm that a named
        # person was taken down.
        if practitioner is None or not is_publicly_visible(practitioner):
            raise forms.ValidationError(LISTING_NOT_FOUND)

        self.practitioner = practitioner
        return raw

    @staticmethod
    def _by_slug(raw: str) -> Practitioner | None:
        candidate = raw.strip().strip("/")
        if "/p/" in candidate:
            candidate = candidate.split("/p/", 1)[1].strip("/").split("/")[0]
        elif "/" in candidate or " " in candidate:
            return None
        return Practitioner.objects.filter(slug=candidate).first() if candidate else None

    @staticmethod
    def _by_name(raw: str) -> Practitioner | None:
        """Exact name, and only when it identifies one person.

        Two practitioners with the same name is not a hypothetical, and guessing
        which one a report is about would put a concern on the wrong person's
        record. Ambiguity fails to the "paste the link" message.
        """
        matches = list(Practitioner.objects.filter(full_name__iexact=raw)[:2])
        return matches[0] if len(matches) == 1 else None

    @property
    def is_bot(self) -> bool:
        return bool(self.cleaned_data.get("website"))

    @property
    def category_options(self) -> list[dict]:
        """Choices in the shape kiam-ui's field partial reads.

        It wants ``{"value", "label"}`` dicts. Handing it Django's (value, label)
        tuples renders the whole tuple as the option's value — silently, and only
        visible in the posted data.
        """
        return [{"value": value, "label": label} for value, label in self.fields["category"].choices]
