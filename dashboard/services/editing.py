"""Saving a practitioner's own edits, and deciding what that costs them.

This module exists for one sentence in the brief: *"the practitioner should know
before saving which kind of change they're making."* Everything here is in service
of making that promise true rather than decorative.

**Two kinds of field, and the split is not ours to invent.**
``backoffice.services.review.CONTROLLED_FIELDS`` is the definition, written at
Phase 2 and enforced in admin since. This module derives from it rather than
restating it, so the label a practitioner reads next to a form field and the
behaviour they get when they press Save cannot disagree.

**"Re-enters review" does NOT mean the listing comes down**, and the UI has to say
so. ``review.submit_update()`` keeps the page PUBLISHED and withdraws the *badge*:
pulling a live listing because somebody corrected the spelling of their own surname
would punish keeping a listing accurate, and what is actually in doubt is Kiam's
claim to have checked the details. A practitioner who reads "this will send your
listing back for review" and pictures their page disappearing will not fix their
own typo, which is the opposite of what the control is for. The copy says: stays
online, badge removed until re-checked.

**The badge is not switched off, because it cannot be.** ``submit_update`` reopens
the checks made against whatever changed and ``verification.recompute()`` drops
``is_verified`` because a required check is no longer VERIFIED. Nothing in this
module writes a verification field, and nothing may.

**Changed-field detection is the whole correctness problem.** A controlled edit
that this module fails to notice is a badge that stays up against unchecked
details — the exact failure the Phase 3b design exists to prevent. So:

* row fields come from ``form.changed_data``, which Django computes by comparing
  the submitted value against the form's ``initial``;
* ``client_groups`` is a ModelForm M2M and is in ``changed_data`` too;
* ``registrations`` and ``qualifications`` are separate models edited through
  formsets, so they are detected from ``formset.has_changed()`` and passed in
  explicitly. A formset the caller forgets to declare is a silent hole, which is
  why ``save()`` takes them as a named argument rather than guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import transaction

from backoffice.services import review
from directory.services import completeness

logger = logging.getLogger("dashboard.editing")

#: The authority, imported rather than restated.
CONTROLLED = frozenset(review.CONTROLLED_FIELDS)

#: What a practitioner is told about each kind of change, *before* they save.
#:
#: Deliberately concrete about the consequence. "Requires review" tells somebody
#: nothing about whether their page is about to vanish, which is the thing they
#: are actually worried about.
#:
#: TWO lengths, and the split came from looking at the rendered page. The full
#: version once per page, in the legend; the short one on each marked field. The
#: first draft used the long one in both places, so on the profile editor the same
#: two-line paragraph appeared five times — visual noise for a sighted reader, and
#: for somebody tabbing through with a screen reader, the identical sentence read
#: out four times between four inputs.
CONTROLLED_CONSEQUENCE = (
    "Changing this asks us to check it again. Your listing stays online, but the "
    "“Credentials checked” badge comes off until we have re-checked the evidence "
    "behind it."
)
CONTROLLED_CONSEQUENCE_SHORT = (
    "Change this and we re-check it. Your listing stays online; the badge comes off until we have."
)
SAFE_CONSEQUENCE = "Changes here go live as soon as you save."


def is_controlled(field_name: str) -> bool:
    return field_name in CONTROLLED


def classify(field_names) -> dict[str, bool]:
    """``{field: is_controlled}`` for a form's fields, for the template."""
    return {name: is_controlled(name) for name in field_names}


@dataclass
class Outcome:
    """What happened, in the terms the practitioner needs to be told."""

    changed: list[str] = field(default_factory=list)
    controlled: list[str] = field(default_factory=list)
    review_request = None
    #: True when this edit actually took a live badge off. Distinct from
    #: ``controlled`` being non-empty: a listing that had no badge loses nothing,
    #: and telling somebody their badge has been withdrawn when they never had one
    #: is a confusing lie.
    badge_withdrawn: bool = False

    @property
    def went_live(self) -> bool:
        """Whether the visible listing changed the moment they pressed Save."""
        return bool(self.changed) and not self.controlled

    @property
    def nothing_changed(self) -> bool:
        return not self.changed


def controlled_labels(form, names) -> list[str]:
    """Human labels for a set of changed field names, for the confirmation message.

    Falls back to the raw name for the two related-model axes, which are formsets
    rather than form fields and so have no label to read.
    """
    labels = []
    for name in names:
        bound = form.fields.get(name) if form is not None else None
        labels.append(str(bound.label) if bound is not None and bound.label else name.replace("_", " "))
    return labels


@transaction.atomic
def save(form, *, practitioner, actor, related_changed=()) -> Outcome:
    """Persist an editor form and route the consequence.

    Args:
        form: a bound, valid ``ModelForm`` over ``Practitioner``.
        practitioner: the row being edited. Ownership is the view's business —
            ``accounts.access.owns_practitioner`` — not this function's.
        actor: who is editing. Recorded on the audit entry.
        related_changed: names from ``CONTROLLED_FIELDS`` whose *formsets* changed
            (``registrations``, ``qualifications``). The caller must pass these;
            see the module docstring on why they are not inferred.

    Returns an ``Outcome``. Raises nothing on a no-op edit — pressing Save without
    typing anything is a normal thing to do and gets an honest "nothing to change".
    """
    changed = sorted(set(form.changed_data) | set(related_changed))
    controlled = review.controlled_changes(changed)

    had_badge = practitioner.is_verified

    form.save()
    # M2M and formsets have their own signals, but Qualification and Registration
    # rows are different models entirely and signal nothing about the practitioner.
    # Recomputing here covers every editor with one call.
    practitioner.refresh_from_db()
    completeness.recompute(practitioner)

    outcome = Outcome(changed=changed, controlled=controlled)

    if controlled:
        # Only meaningful on a live listing. On a draft there is no badge to
        # withdraw and no page to protect, and `submit_update` returns None —
        # the ordinary submit path applies when they are ready.
        outcome.review_request = review.submit_update(practitioner, changed_fields=controlled, actor=actor)
        practitioner.refresh_from_db()
        outcome.badge_withdrawn = had_badge and not practitioner.is_verified

    logger.info(
        "dashboard.saved",
        extra={
            "practitioner_id": str(practitioner.pk),
            "changed": changed,
            "controlled": controlled,
            "badge_withdrawn": outcome.badge_withdrawn,
            "status": practitioner.status,
        },
    )
    return outcome


def message_for(outcome: Outcome, form=None) -> tuple[str, str]:
    """``(level, text)`` for ``django.contrib.messages``.

    The wording is the whole point of this module, so it is here rather than
    scattered through the views: one place where "what just happened to my
    listing" is decided, and one place a reviewer can read it.
    """
    if outcome.nothing_changed:
        return "info", "Nothing to save — that all looks the same as before."

    if not outcome.controlled:
        return "success", "Saved. Your listing is updated."

    labels = controlled_labels(form, outcome.controlled)
    what = ", ".join(labels)

    if outcome.badge_withdrawn:
        return (
            "warning",
            f"Saved. Because you changed {what}, we need to check it again — so the "
            "“Credentials checked” badge has come off your listing for now. "
            "Your listing is still online. We will email you when it is back.",
        )

    if outcome.review_request is not None:
        return (
            "success",
            f"Saved. {what.capitalize()} is one of the details we check, so it is "
            "queued for us to look at. Your listing is still online.",
        )

    # A draft or a listing awaiting review: nothing to withdraw, nothing queued.
    return "success", "Saved."
