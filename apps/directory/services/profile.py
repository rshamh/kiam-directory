"""The public profile — resolution, gating and the read-only view model.

Everything a visitor is allowed to see about a practitioner is assembled here,
so the template renders what it is given and decides nothing.

Three rules live in this module, and all three are load-bearing.

**One question decides visibility.** ``backoffice.services.publication
.is_publicly_visible()`` — a bare boolean — and nothing else. The view does not
inspect ``status``, does not special-case SUSPENDED, and does not explain
itself: a suspended listing and a slug that never existed both 404, with the
same page and the same status code. Anything else tells the public that a named
person was taken down, which is a defamation risk against someone who may be
cleared next week. The reason is in the audit log, for staff.

**Client groups come through the gate.** ``visible_client_groups()`` is the only
route to them. A PROVISIONAL practitioner is live for adult work only, and their
under-18 groups must be *absent from the HTML* — not hidden with CSS, not
present in a data attribute, not sitting in a context variable a later template
edit might print. That is why this module returns the filtered queryset rather
than the practitioner's own ``client_groups``.

**Contact details are not in the view model.** ``available_channels()`` returns
which channels exist, never their values; ``channel_value()`` is a separate call
the reveal view makes. Keeping them apart means the profile template *cannot*
leak an email address into the initial HTML even by accident, which is the only
thing the reveal is actually protecting.

Note what is NOT gated here: specialities. ``Speciality.implies_minors`` marks a
speciality as implying under-18 work, but the DBS gate in
``Practitioner.can_show_minor_groups`` is defined on client groups alone, and so
is the Phase 4 search filter. Adding a speciality gate here and not there would
recreate exactly the one-sided leak CLAUDE.md warns about. Flagged for the phase
gate rather than fixed unilaterally.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import reverse

from apps.backoffice.services.publication import is_publicly_visible
from apps.directory.models import DeliveryMode, Practitioner, SlugRedirect

#: How many specialities the answer sentence names before it stops. The first
#: paragraph of a profile is what an answer engine extracts (docs/seo.md, "AEO"),
#: and a sentence listing eleven specialities answers nothing.
SUMMARY_SPECIALITY_LIMIT = 3

#: Meta descriptions are truncated around here by every search engine.
META_DESCRIPTION_LIMIT = 155

#: The three contact channels, and how the profile page offers them. Values are
#: deliberately absent — see the module docstring. ``metrics.FIELD_BY_CHANNEL``
#: keys off the same three strings and a test asserts the two agree.
CHANNELS = {
    "email": {
        "key": "email",
        "label": "Email address",
        "button": "Show email address",
        "icon": "mail",
        "field": "public_email",
    },
    "phone": {
        "key": "phone",
        "label": "Phone number",
        "button": "Show phone number",
        "icon": "phone",
        "field": "public_phone",
    },
    "website": {
        "key": "website",
        "label": "Website",
        "button": "Show website address",
        "icon": "external-link",
        "field": "public_website",
    },
}

#: Accessibility features, in the order they read best on a location card.
ACCESS_FEATURES = (
    ("step_free_access", "Step-free access"),
    ("wheelchair_access", "Wheelchair accessible"),
    ("hearing_loop", "Hearing loop"),
    ("parking_available", "Parking available"),
    ("near_public_transport", "Near public transport"),
)

DELIVERY_PHRASE = {
    DeliveryMode.IN_PERSON: "in person",
    DeliveryMode.ONLINE: "online",
    DeliveryMode.BOTH: "in person and online",
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """What a slug resolved to.

    Exactly one of the two is set, or neither — and "neither" is the answer for
    a suspended listing, an unpublished one, a draft, and a slug nobody has ever
    used. The caller turns all of those into the same 404.
    """

    practitioner: Practitioner | None = None
    redirect_to: str | None = None


def profile_path(practitioner: Practitioner) -> str:
    return reverse("directory:profile", kwargs={"slug": practitioner.slug})


def resolve(slug: str) -> Resolution:
    """Find the listing a slug points at, following one redirect if needed.

    A live slug always wins over a redirect row, so a slug that was retired and
    later reused cannot bounce a visitor away from the listing that currently
    holds it.
    """
    practitioner = Practitioner.objects.select_related("profession").filter(slug=slug).first()
    if practitioner is not None:
        if is_publicly_visible(practitioner):
            return Resolution(practitioner=practitioner)
        # Exists, but not publicly. Same answer as never existed.
        return Resolution()

    redirect = SlugRedirect.objects.select_related("practitioner").filter(old_slug=slug).first()
    if redirect is not None and is_publicly_visible(redirect.practitioner):
        return Resolution(redirect_to=profile_path(redirect.practitioner))

    # A redirect pointing at a listing that is no longer public is worth no more
    # than a 301 into a 404, and a 301 would confirm the listing once existed.
    return Resolution()


# ---------------------------------------------------------------------------
# The view model
# ---------------------------------------------------------------------------


def display_name(practitioner: Practitioner) -> str:
    """Title and name, as it should read in a heading."""
    return " ".join(part for part in (practitioner.display_title, practitioner.full_name) if part)


def build(practitioner: Practitioner) -> dict:
    """Everything the profile template renders, and nothing else."""
    locations = public_locations(practitioner)
    return {
        "practitioner": practitioner,
        "display_name": display_name(practitioner),
        # THE GATE. Never `practitioner.client_groups`.
        "client_groups": practitioner.visible_client_groups(),
        "specialities": practitioner.specialities.select_related("category"),
        "approaches": practitioner.approaches.all(),
        "languages": practitioner.languages.all(),
        "funding_options": practitioner.funding_options.all(),
        "session_formats": practitioner.session_formats.all(),
        "qualifications": practitioner.qualifications.all(),
        "registrations": practitioner.registrations.all(),
        "locations": [
            {"location": location, "access_features": access_features(location)} for location in locations
        ],
        "delivery_phrase": DELIVERY_PHRASE.get(practitioner.delivery_mode, ""),
        "fee_range": fee_range(practitioner),
        "answer_sentence": answer_sentence(practitioner),
        "channels": available_channels(practitioner),
    }


def public_locations(practitioner: Practitioner) -> list:
    """Addresses the practitioner agreed to publish.

    ``is_public=False`` means the address feeds the search radius only. Rendering
    one would publish a home address somebody deliberately withheld.
    """
    return list(practitioner.locations.filter(is_public=True))


def access_features(location) -> list[str]:
    return [label for field, label in ACCESS_FEATURES if getattr(location, field, False)]


def fee_range(practitioner: Practitioner) -> str:
    """Fees, in pounds. Stored in pence; never rendered raw."""
    low, high = practitioner.fee_min, practitioner.fee_max
    if low and high and low != high:
        return f"£{_pounds(low)}–£{_pounds(high)} per session"
    if low and high:
        return f"£{_pounds(low)} per session"
    if low:
        return f"From £{_pounds(low)} per session"
    if high:
        return f"Up to £{_pounds(high)} per session"
    return ""


def _pounds(pence: int) -> str:
    pounds, remainder = divmod(int(pence), 100)
    return f"{pounds:,}" if remainder == 0 else f"{pounds:,}.{remainder:02d}"


def answer_sentence(practitioner: Practitioner) -> str:
    """The first paragraph, written to be extracted.

    ``docs/seo.md`` is explicit about the shape: "Dr X is an independent
    consultant psychiatrist offering adult ADHD assessment in Epsom, in person
    and online". That is the sentence an answer engine lifts, so it is generated
    from the structured fields rather than hoped for in free text — and it opens
    with *independent*, which is the word that must survive being quoted out of
    context.

    Names are never lower-cased. "Adult ADHD assessment" and "CBT" are labels,
    and case-folding them for grammar's sake mangles the terms people search for.
    """
    name = display_name(practitioner)
    profession = practitioner.profession.name if practitioner.profession else "practitioner"
    # "an" agrees with "independent", not with the profession that follows it.
    # Deriving the article from the profession produced "is a independent
    # Consultant Psychiatrist" — right rule, wrong word.
    sentence = f"{name} is an independent {profession}"

    specialities = [s.name for s in practitioner.specialities.all()[:SUMMARY_SPECIALITY_LIMIT]]
    if specialities:
        sentence += f" offering {humanised(specialities)}"

    towns = _distinct([location.city for location in public_locations(practitioner) if location.city])
    if towns and practitioner.delivery_mode != DeliveryMode.ONLINE:
        sentence += f" in {humanised(towns)}"

    phrase = DELIVERY_PHRASE.get(practitioner.delivery_mode, "")
    if phrase:
        sentence += f", {phrase}"

    return sentence + "."


#: Appended when there is room for it. Counted against the budget rather than
#: added after it — truncating the sentence to 155 and then appending 56 more
#: characters produced 212-character descriptions that Google cut mid-sentence,
#: leaving a stray ellipsis stranded in the middle of the snippet.
META_DESCRIPTION_SUFFIX = "Contact them directly through the Kiam Clinic Directory."


def meta_description(practitioner: Practitioner) -> str:
    """The answer sentence, and the call to action if it fits."""
    sentence = answer_sentence(practitioner)

    if len(sentence) + 1 + len(META_DESCRIPTION_SUFFIX) <= META_DESCRIPTION_LIMIT:
        return f"{sentence} {META_DESCRIPTION_SUFFIX}"

    # No room for both. The sentence is what answers the searcher's question, so
    # it is the one that survives; the suffix says nothing they cannot work out
    # from having arrived.
    if len(sentence) > META_DESCRIPTION_LIMIT:
        sentence = sentence[:META_DESCRIPTION_LIMIT].rsplit(" ", 1)[0].rstrip(",") + "…"
    return sentence


def humanised(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _distinct(values: list[str]) -> list[str]:
    seen, out = set(), []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


# ---------------------------------------------------------------------------
# Contact channels
# ---------------------------------------------------------------------------


def available_channels(practitioner: Practitioner) -> list[dict]:
    """Which channels this practitioner published — WITHOUT their values."""
    return [dict(spec) for spec in CHANNELS.values() if channel_value(practitioner, spec["key"])]


def channel_value(practitioner: Practitioner, channel: str) -> str:
    """The detail behind one channel. Called by the reveal view, and nowhere else."""
    spec = CHANNELS.get(channel)
    if spec is None:
        return ""
    return (getattr(practitioner, spec["field"], "") or "").strip()


def channel_href(channel: str, value: str) -> str:
    """A usable link for a revealed detail.

    Phone numbers keep their spaces on screen and lose them in the ``tel:``
    href — a dialler cannot parse "01372 660 580", and a reader should not have
    to.
    """
    if channel == "email":
        return f"mailto:{value}"
    if channel == "phone":
        return "tel:" + "".join(c for c in value if c.isdigit() or c == "+")
    return value
