"""The practitioner's own editors.

Public-facing, so the opposite of ``backoffice/forms.py``: plain language, help
text that says why rather than what, and no assumed domain knowledge. This
audience has a high rate of anxiety and neurodevelopmental conditions (golden rule
#3) and is also, at this moment, a professional being asked about their own
credentials — so nothing here is terse and nothing is a bare validation error.

Three things in this file are compliance controls rather than form plumbing.

**The lint runs here.** ``directory.services.lint`` was written for
``review.submit()`` — the one-time path from draft to published. Phase 6 is the
first time a practitioner can edit a **live** listing's free text, and a safe-field
edit publishes immediately, so without this a published practitioner could put a
prescription-only medicine name into their intro and it would be public the moment
they pressed Save. ``docs/content-compliance.md`` §1 says a match "blocks
submission"; this is what makes that true of the second and every subsequent edit,
not just the first. See ``LintedPractitionerForm``.

**Fees are pence in the database and pounds on the screen.** A practitioner typing
"90" means ninety pounds. Storing 90 pence and then filtering "up to £100" against
it would silently put everyone in the cheapest bracket.

**A location must geocode before it can be saved.** ``PractitionerLocation.geo`` is
non-null and it is what the radius search matches on, so an address that cannot be
placed is an address nobody will ever be found at. Refusing the save with "we could
not find that postcode" is honest; saving it at coordinates nobody chose is not.
"""

from __future__ import annotations

from django import forms
from django.forms import inlineformset_factory

from directory.models import (
    Approach,
    ClientGroup,
    DeliveryMode,
    FundingOption,
    Gender,
    Language,
    Practitioner,
    PractitionerLocation,
    Profession,
    Qualification,
    Registration,
    SessionFormat,
    Speciality,
    VerificationType,
)
from directory.services import lint

from .services import editing

# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------


def describe_fields(form) -> None:
    """Wire `aria-describedby` from every control to its own help and consequence.

    Django 5 adds `aria-describedby` for help text automatically **only** when the
    form is rendered through its own `div.html` template. These templates render
    `{{ field }}` inside their own markup — because kiam-ui's field partial drops
    help text on error (docs/design-system.md gap 12) — so the attribute has to be
    set here or the "we check this" consequence and the help text are visible to a
    sighted user and invisible to a screen reader.

    Ids match what `_field.html` emits. Errors are not included: Django sets
    `aria-invalid` itself, and the error `<p>` is rendered before the control, so
    it is read on the way past.
    """
    for name, field in form.fields.items():
        auto_id = form[name].id_for_label
        described = []
        if editing.is_controlled(name):
            described.append(f"{auto_id}-controlled")
        if field.help_text:
            described.append(f"{auto_id}-help")
        if described:
            field.widget.attrs["aria-describedby"] = " ".join(described)


class PoundsField(forms.DecimalField):
    """Pounds on the screen, pence in the column.

    ``fee_min``/``fee_max`` are ``PositiveIntegerField`` in pence, and
    ``search.FEE_OPTIONS`` filters on pence. A form that wrote pounds into them
    would put every practitioner in the "under £60" bracket, quietly.
    """

    def __init__(self, **kwargs):
        kwargs.setdefault("max_digits", 8)
        kwargs.setdefault("decimal_places", 2)
        kwargs.setdefault("min_value", 0)
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if value in (None, ""):
            return value
        try:
            return round(int(value) / 100, 2)
        except (TypeError, ValueError):
            return value

    def clean(self, value):
        pounds = super().clean(value)
        if pounds is None:
            return None
        return int(round(pounds * 100))


class LintedPractitionerForm(forms.ModelForm):
    """A practitioner ModelForm that refuses copy the content rules block.

    Runs in ``_post_clean``, which is after Django has copied the cleaned values
    onto ``self.instance`` and before anything is written — so the lint sees
    exactly what would be saved.

    **Only findings about fields this form edits become errors.** Linting the whole
    practitioner is right (the rules are about the listing, not the form), but
    surfacing all of it here would let a prescription-only name in an intro block
    somebody from editing their opening hours in a different tab, with an error
    pointing at a field that is not on the page. Findings about other fields are
    kept on ``self.lint_result`` so a caller can still see them.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lint_result = None

    def _post_clean(self):
        super()._post_clean()

        self.lint_result = lint.run(self.instance)

        for finding in self.lint_result.blocks:
            if finding.field in self.fields:
                self.add_error(finding.field, finding.message)
            elif finding.field in lint.LINTED_FIELDS or finding.field in lint.TITLE_FIELDS:
                # About a field this form does not show: not this editor's problem
                # to block on. It will still block submission.
                continue

    @property
    def held_findings(self) -> list:
        """HOLD-severity findings for fields on this form.

        Not errors — they do not stop the save. Shown as a warning so a
        practitioner is told their wording will be looked at before it goes live,
        rather than discovering it from a review note days later.
        """
        if self.lint_result is None:
            return []
        return [f for f in self.lint_result.holds if f.field in self.fields]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileForm(LintedPractitionerForm):
    """Who you are and how to reach you.

    Controlled here: ``full_name``, ``display_title``, ``post_nominals`` and
    ``profession``. Each is checked against a document, so changing one asks for
    that document to be looked at again.
    """

    class Meta:
        model = Practitioner
        fields = (
            "full_name",
            "display_title",
            "post_nominals",
            "pronouns",
            "gender",
            "profession",
            "years_experience",
            "qualified_since",
            "headshot",
            "intro",
            "services",
            "public_email",
            "public_phone",
            "public_website",
        )
        labels = {
            "full_name": "Your name",
            "display_title": "Title",
            "post_nominals": "Letters after your name",
            "pronouns": "Pronouns",
            "gender": "Gender",
            "profession": "Profession",
            "years_experience": "Years in practice",
            "qualified_since": "Year you qualified",
            "headshot": "Photo",
            "intro": "About you",
            "services": "What you offer",
            "public_email": "Email address for clients",
            "public_phone": "Phone number for clients",
            "public_website": "Your website",
        }
        help_texts = {
            "display_title": "Dr, Prof, Mr, Ms — whatever you use professionally. Leave it blank if you'd rather not.",
            "post_nominals": "For example: MBBS MRCPsych, or BACP (Accred).",
            "pronouns": "Shown on your listing if you fill it in. Leave it blank if you'd rather not.",
            "gender": "Some clients search on this. Answering is optional.",
            "years_experience": "Roughly is fine.",
            "headshot": "A clear photo of your face. Listings with a photo are the ones people click.",
            "intro": (
                "The paragraph almost everyone reads before deciding whether to contact you. "
                "Around 100–150 words. Write it as though you were talking to one person."
            ),
            "services": "What an appointment with you actually involves, and what you can help with.",
            "public_email": "Shown to clients after they click to reveal it, so it is not scraped.",
            "public_phone": "Same — revealed on request, not printed on the page.",
            "public_website": "Optional.",
        }
        widgets = {
            "intro": forms.Textarea(attrs={"rows": 8}),
            "services": forms.Textarea(attrs={"rows": 6}),
            "gender": forms.Select(choices=[("", "Prefer not to say"), *Gender.choices]),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["profession"].queryset = Profession.objects.filter(active=True)
        self.fields["profession"].empty_label = "Choose your profession"
        for name in ("pronouns", "gender", "years_experience", "qualified_since", "post_nominals"):
            self.fields[name].required = False
        describe_fields(self)


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


class LocationForm(forms.ModelForm):
    """One practice address, geocoded on save.

    ``is_public`` is the interesting field and its help text is doing real work:
    a practitioner working from home needs to be findable by distance without
    publishing their home address, and if they do not know that is an option they
    will either publish it or not list an address at all.
    """

    class Meta:
        model = PractitionerLocation
        fields = (
            "label",
            "address_line1",
            "address_line2",
            "city",
            "county",
            "postcode",
            "is_public",
            "is_primary",
            "days_at_site",
            "step_free_access",
            "wheelchair_access",
            "parking_available",
            "hearing_loop",
            "near_public_transport",
        )
        labels = {
            "label": "Name for this place",
            "address_line1": "Address",
            "address_line2": "Address line 2",
            "city": "Town or city",
            "county": "County",
            "postcode": "Postcode",
            "is_public": "Show this address on my listing",
            "is_primary": "This is my main place of work",
            "days_at_site": "Days you are here",
            "step_free_access": "Step-free access",
            "wheelchair_access": "Wheelchair accessible",
            "parking_available": "Parking available",
            "hearing_loop": "Hearing loop",
            "near_public_transport": "Near public transport",
        }
        help_texts = {
            "label": "For example: “Epsom practice”. Only for your own reference if you have several.",
            "postcode": "We use this to place you on the map, so people searching nearby can find you.",
            "is_public": (
                "Untick this if you work from home. We will still match you to people "
                "searching in your area, but the address itself stays private."
            ),
            "days_at_site": "For example: “Tue, Thu”. Optional.",
            "step_free_access": (
                "Please only tick these if they are true of this address — people filter on them "
                "and travel on the strength of them."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("label", "address_line2", "county", "days_at_site"):
            self.fields[name].required = False
        describe_fields(self)

    def clean(self):
        """Place the address, or refuse it.

        ``geo`` is non-null and it is the only thing radius search matches on, so
        an ungeocodable address is one nobody will ever be found at. Better to say
        so than to store a location that silently does nothing.
        """
        cleaned = super().clean()
        postcode = (cleaned.get("postcode") or "").strip()

        if not postcode:
            return cleaned

        from django.contrib.gis.geos import Point

        from search.services import geocode

        try:
            place = geocode.resolve(postcode)
        except Exception:  # noqa: BLE001 — geocode.resolve is built not to raise; belt and braces
            place = None

        if place is None:
            raise forms.ValidationError(
                {
                    "postcode": (
                        "We could not find that postcode, so we cannot place you on the map. "
                        "Check it and try again — a full postcode works best. If it is right "
                        "and this keeps happening, email us and we will sort it out."
                    )
                }
            )

        self.instance.geo = Point(place.lng, place.lat, srid=4326)
        return cleaned


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

#: The four "other" boxes, and the axis each writes a ``TaxonomyRequest`` for.
OTHER_AXES = (
    ("speciality", "specialities", "something you work with"),
    ("approach", "approaches", "an approach you use"),
    ("language", "languages", "a language you work in"),
    ("funding", "funding_options", "a way clients can pay"),
)


class TaxonomyForm(forms.ModelForm):
    """What you work with, how, and who with.

    Structured pickers over the controlled vocabulary, because the vocabulary is
    what search matches on — free text here would produce listings that cannot be
    found. The "other" boxes are the escape hatch: they write a
    ``TaxonomyRequest`` for an admin to triage rather than adding a term nobody
    can search for.

    ``client_groups`` is CONTROLLED, and it is the one on this page that matters
    most: adding an under-18 group is what makes an enhanced DBS check required at
    all.
    """

    class Meta:
        model = Practitioner
        fields = (
            "specialities",
            "approaches",
            "client_groups",
            "languages",
            "funding_options",
            "session_formats",
        )
        labels = {
            "specialities": "What you work with",
            "approaches": "Approaches you use",
            "client_groups": "Who you work with",
            "languages": "Languages you work in",
            "funding_options": "How clients can pay",
            "session_formats": "Session formats",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for name, queryset in (
            ("specialities", Speciality.objects.filter(active=True).select_related("category")),
            ("approaches", Approach.objects.filter(active=True)),
            ("client_groups", ClientGroup.objects.filter(active=True)),
            ("languages", Language.objects.filter(active=True)),
            ("funding_options", FundingOption.objects.filter(active=True)),
            ("session_formats", SessionFormat.objects.filter(active=True)),
        ):
            self.fields[name].queryset = queryset
            self.fields[name].widget = forms.CheckboxSelectMultiple()
            self.fields[name].widget.choices = [(o.pk, str(o)) for o in queryset]
            self.fields[name].required = False

        for axis, _field, what in OTHER_AXES:
            self.fields[f"other_{axis}"] = forms.CharField(
                label="Something missing?",
                required=False,
                help_text=(
                    f"If we do not list {what}, type it here and we will look at adding it. "
                    "It will not appear on your listing until we do."
                ),
            )

        describe_fields(self)

    def taxonomy_requests(self) -> list[tuple[str, str]]:
        """``(axis, term)`` for each "other" box that was filled in."""
        return [
            (axis, self.cleaned_data.get(f"other_{axis}", "").strip())
            for axis, _field, _what in OTHER_AXES
            if self.cleaned_data.get(f"other_{axis}", "").strip()
        ]


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class CredentialsForm(forms.ModelForm):
    """Prescribing status. The qualifications and registrations are formsets."""

    class Meta:
        model = Practitioner
        fields = ("is_prescriber",)
        labels = {"is_prescriber": "I can prescribe medication"}
        help_texts = {
            "is_prescriber": (
                "Tick this only if you hold prescribing rights. We check it against your "
                "register entry before it shows on your listing."
            )
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        describe_fields(self)


QualificationFormSet = inlineformset_factory(
    Practitioner,
    Qualification,
    fields=("title", "institution", "year"),
    labels={"title": "Qualification", "institution": "Where you studied", "year": "Year"},
    extra=1,
    can_delete=True,
)

RegistrationFormSet = inlineformset_factory(
    Practitioner,
    Registration,
    fields=("body", "registration_no", "register_url"),
    labels={
        "body": "Regulator or professional body",
        "registration_no": "Registration number",
        "register_url": "Link to your entry on their public register",
    },
    extra=1,
    can_delete=True,
)


# ---------------------------------------------------------------------------
# Availability and fees
# ---------------------------------------------------------------------------


class AvailabilityForm(LintedPractitionerForm):
    """Everything on this page publishes immediately. Nothing here is controlled."""

    fee_min = PoundsField(label="Fee from (£)", required=False)
    fee_max = PoundsField(label="Fee up to (£)", required=False)

    class Meta:
        model = Practitioner
        fields = (
            "delivery_mode",
            "offers_online",
            "online_coverage",
            "accepting_new_clients",
            "typical_wait",
            "evening_appointments",
            "weekend_appointments",
            "availability_note",
            "fee_min",
            "fee_max",
            "free_initial_call",
            "offers_sliding_scale",
        )
        labels = {
            "delivery_mode": "How you see clients",
            "offers_online": "I offer online appointments",
            "online_coverage": "Where you can work online",
            "accepting_new_clients": "I am taking on new clients",
            "typical_wait": "Typical wait for a first appointment",
            "evening_appointments": "Evening appointments",
            "weekend_appointments": "Weekend appointments",
            "availability_note": "Anything else about your availability",
            "free_initial_call": "I offer a free introductory call",
            "offers_sliding_scale": "I offer reduced fees for some clients",
        }
        help_texts = {
            "online_coverage": "For example: “UK-wide”. Clients searching from far away need to know.",
            "accepting_new_clients": (
                "Untick this when you are full. Your listing stays up and stays findable — it "
                "just says you are not taking anyone on."
            ),
            "typical_wait": "An honest estimate. This is one of the things people filter on.",
            "fee_min": "Your usual fee, or the lower end if it varies. One of the two filters people use most.",
            "fee_max": "Leave blank if you have a single rate.",
            "availability_note": "Optional. For example: “Tuesdays and Thursdays only.”",
        }
        widgets = {
            "availability_note": forms.Textarea(attrs={"rows": 2}),
            "delivery_mode": forms.RadioSelect(choices=DeliveryMode.choices),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["typical_wait"].required = False
        self.fields["online_coverage"].required = False
        self.fields["availability_note"].required = False
        describe_fields(self)

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("fee_min"), cleaned.get("fee_max")
        if low is not None and high is not None and high < low:
            self.add_error("fee_max", "The upper fee cannot be lower than the fee you start from.")
        return cleaned


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceUploadForm(forms.Form):
    """One document, for one check.

    The file itself is validated by ``directory.services.antivirus`` inside
    ``documents.upload()`` rather than here: size, type and the virus scan are one
    question asked at one moment, and asking half of it in a form and half in a
    service is how the halves drift apart.
    """

    check_type = forms.ChoiceField(label="What is this?", choices=[])
    file = forms.FileField(
        label="Choose the file",
        help_text="A PDF or a clear photo. Up to 25 MB.",
    )

    def __init__(self, *args, choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["check_type"].choices = choices or [
            (value, dict(VerificationType.choices)[value])
            for value in ("identity", "registration", "qualification", "insurance")
        ]


# ---------------------------------------------------------------------------
# Account and security
# ---------------------------------------------------------------------------


class EmailChangeForm(forms.Form):
    new_email = forms.EmailField(
        label="New email address",
        help_text=(
            "We will email both your current address and the new one. The change only "
            "happens once you have confirmed at both — that is what stops somebody moving "
            "your account somewhere you cannot reach, and what stops a typo locking you out."
        ),
    )


class ConfirmActionForm(forms.Form):
    """A typed confirmation for the two irreversible-feeling actions.

    Not a checkbox. Taking a listing down is the thing a practitioner is most
    likely to do by accident from a keyboard, and the thing they will be most
    upset about — and full removal schedules their evidence for deletion. Typing
    the word is a beat of deliberation, and it is the pattern people already know
    from other services.
    """

    WORD = "REMOVE"

    confirm = forms.CharField(label="Type REMOVE to confirm", strip=True)
    reason = forms.CharField(
        label="Anything you want to tell us? (optional)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Not required, and it does not delay anything. It helps us improve the directory.",
    )

    def clean_confirm(self):
        value = (self.cleaned_data.get("confirm") or "").strip().upper()
        if value != self.WORD:
            raise forms.ValidationError(f"Type {self.WORD} exactly, in capitals, to confirm.")
        return value
