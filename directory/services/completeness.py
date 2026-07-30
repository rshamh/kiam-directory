"""How complete a listing is, and what specifically is missing from it.

``Practitioner.completeness`` has existed since Phase 1 as a "denormalised,
recomputed on write" ranking input. **Nothing computed it.** It carries 15% of the
search score (``search.W_COMPLETENESS``) and Phase 5 made it the gate on the
home-page grid (``HOMEPAGE_MIN_COMPLETENESS``), so in production every real
listing would have sat at the default 0: last on every ranking tie-break and
absent from the front page entirely. `seed_demo` hard-codes plausible numbers and
the test factory defaults to 80, which is exactly why nobody noticed. This module
is the writer, and Phase 6 is where it belongs because the dashboard is the first
thing that has to *show* a practitioner what is missing.

**One writer, like verification.** Everything goes through ``recompute()``, which
writes through a queryset ``.update()`` so it emits no ``post_save`` and cannot
recurse from the signal that calls it. Nothing else may write the column.

**The score and the advice are the same list.** A meter that says "68%" and a
checklist that says what to do next must never disagree, so they are derived from
one another: ``REQUIREMENTS`` is the whole definition, and both the number and the
"add a photo" line come out of walking it once. A percentage on its own is a
guilt-trip; the number is only useful because the next click is next to it.

**Weights are what a client needs, not what the form asks for.** The intro
paragraph and a contact route carry more than post-nominals, because a listing
with no way to contact anybody is not 95% of a listing. They sum to exactly 100
and a test asserts it, so adding a requirement forces a deliberate decision about
what it is worth relative to everything else rather than quietly rescaling the
meter for every practitioner on the site.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from directory.models import Practitioner

logger = logging.getLogger("directory.completeness")


@dataclass(frozen=True)
class Requirement:
    """One thing a listing needs, what it is worth, and where to go and do it."""

    key: str
    #: Shown when it is DONE. Past tense, factual.
    label: str
    #: Shown when it is MISSING. An instruction, and specific enough to act on
    #: without reading anything else — "Add a photo of yourself", not "Photo".
    action: str
    weight: int
    #: ``dashboard:`` URL name of the editor that fixes it, so the checklist links
    #: straight there instead of leaving somebody to find the right tab.
    editor: str
    test: Callable[[Practitioner], bool]
    #: Why it matters, for the practitioner. Optional, and only where the reason is
    #: not obvious from the action.
    because: str = ""


def _has_contact_route(p: Practitioner) -> bool:
    """Any one of the three is enough — the reveal offers whichever exist."""
    return bool(p.public_email or p.public_phone or p.public_website)


def _is_reachable_somehow(p: Practitioner) -> bool:
    """Either a place to go, or online with the coverage stated.

    ``offers_online`` alone is not enough: "online" with no coverage note leaves a
    client in Aberdeen unable to tell whether they are being offered anything.
    """
    return p.locations.exists() or bool(p.offers_online and p.online_coverage.strip())


#: The whole definition. Weights sum to 100 —
#: ``test_weights_sum_to_one_hundred`` is what stops that drifting.
REQUIREMENTS: tuple[Requirement, ...] = (
    Requirement(
        key="name",
        label="Your name is on the listing",
        action="Add your full name",
        weight=5,
        editor="dashboard:profile",
        test=lambda p: bool(p.full_name.strip()),
    ),
    Requirement(
        key="profession",
        label="Profession set",
        action="Choose your profession",
        weight=5,
        editor="dashboard:profile",
        test=lambda p: p.profession_id is not None,
    ),
    Requirement(
        key="intro",
        label="Introduction written",
        action="Write a short introduction",
        weight=12,
        editor="dashboard:profile",
        test=lambda p: len(p.intro.strip()) >= 200,
        because=(
            "This is the paragraph almost everyone reads before deciding whether to "
            "contact you. Around 100–150 words works well."
        ),
    ),
    Requirement(
        key="services",
        label="Services described",
        action="Describe what you offer",
        weight=8,
        editor="dashboard:profile",
        test=lambda p: len(p.services.strip()) >= 80,
    ),
    Requirement(
        key="headshot",
        label="Photo added",
        action="Add a photo of yourself",
        weight=8,
        editor="dashboard:profile",
        test=lambda p: bool(p.headshot),
        because="Listings with a photo are the ones people click.",
    ),
    Requirement(
        key="contact",
        label="Clients can contact you",
        action="Add an email address, phone number or website",
        weight=10,
        editor="dashboard:profile",
        test=_has_contact_route,
        because="Without one of these your listing gives nobody a way to reach you.",
    ),
    Requirement(
        key="specialities",
        label="Specialities chosen",
        action="Choose what you work with",
        weight=10,
        editor="dashboard:taxonomy",
        test=lambda p: p.specialities.exists(),
        because="This is what most searches match on.",
    ),
    Requirement(
        key="approaches",
        label="Approaches chosen",
        action="Choose the approaches you use",
        weight=6,
        editor="dashboard:taxonomy",
        test=lambda p: p.approaches.exists(),
    ),
    Requirement(
        key="client_groups",
        label="Client groups chosen",
        action="Choose who you work with",
        weight=6,
        editor="dashboard:taxonomy",
        test=lambda p: p.client_groups.exists(),
    ),
    Requirement(
        key="languages",
        label="Languages listed",
        action="List the languages you work in",
        weight=3,
        editor="dashboard:taxonomy",
        test=lambda p: p.languages.exists(),
    ),
    Requirement(
        key="reachable",
        label="Where you work is clear",
        action="Add a practice address, or say where you cover online",
        weight=8,
        editor="dashboard:locations",
        test=_is_reachable_somehow,
    ),
    Requirement(
        key="fees",
        label="Fees given",
        action="Add your fee, or the lower end of your range",
        weight=8,
        editor="dashboard:availability",
        test=lambda p: p.fee_min is not None,
        because=(
            "Fees are one of the two filters people use most. A listing without one "
            "is skipped by anyone who has set a budget."
        ),
    ),
    Requirement(
        key="wait",
        label="Typical wait given",
        action="Say roughly how long people wait for a first appointment",
        weight=4,
        editor="dashboard:availability",
        test=lambda p: bool(p.typical_wait),
    ),
    Requirement(
        key="qualifications",
        label="Qualifications listed",
        action="Add at least one qualification",
        weight=4,
        editor="dashboard:credentials",
        test=lambda p: p.qualifications.exists(),
    ),
    Requirement(
        key="registrations",
        label="Registration listed",
        action="Add your regulator or professional body registration",
        weight=3,
        editor="dashboard:credentials",
        test=lambda p: p.registrations.exists(),
        because="We check this before your listing can show a verification badge.",
    ),
)


@dataclass(frozen=True)
class Item:
    requirement: Requirement
    done: bool

    @property
    def key(self) -> str:
        return self.requirement.key

    @property
    def text(self) -> str:
        return self.requirement.label if self.done else self.requirement.action


@dataclass(frozen=True)
class Report:
    """The meter and the checklist, from one walk of ``REQUIREMENTS``."""

    score: int
    items: tuple[Item, ...]

    @property
    def done(self) -> tuple[Item, ...]:
        return tuple(i for i in self.items if i.done)

    @property
    def missing(self) -> tuple[Item, ...]:
        """What is left, **heaviest first** — so the first line is the one that
        moves the number most, not whichever happens to come first in the form."""
        return tuple(
            sorted(
                (i for i in self.items if not i.done),
                key=lambda i: (-i.requirement.weight, i.requirement.key),
            )
        )

    @property
    def is_complete(self) -> bool:
        return not self.missing

    @property
    def next_action(self) -> Item | None:
        """The single highest-value thing to do next, for a one-line prompt."""
        missing = self.missing
        return missing[0] if missing else None


def report(practitioner: Practitioner) -> Report:
    """Score this listing and say what is missing. Reads; never writes."""
    items = tuple(Item(requirement=r, done=bool(r.test(practitioner))) for r in REQUIREMENTS)
    score = sum(i.requirement.weight for i in items if i.done)
    return Report(score=score, items=items)


def recompute(practitioner: Practitioner) -> int:
    """Write ``completeness`` and return it. **The only writer of that column.**

    Through ``.update()`` rather than ``save()``, so it emits no ``post_save`` and
    the signal that calls it cannot recurse — the same technique
    ``verification.recompute()`` and ``search_index.rebuild()`` use, for the same
    reason.
    """
    score = report(practitioner).score

    if practitioner.completeness != score:
        Practitioner.objects.filter(pk=practitioner.pk).update(completeness=score)
        practitioner.completeness = score
        logger.info(
            "completeness.recomputed",
            extra={"practitioner_id": str(practitioner.pk), "score": score},
        )
    return score


def weights() -> dict[str, int]:
    """Every requirement's weight, for the sum test and the admin."""
    return {r.key: r.weight for r in REQUIREMENTS}
