"""Template helpers for the dashboard.

Both filters exist because ``_field.html`` renders its own markup rather than
using kiam-ui's field partial (design-system gaps 12/13 — that one drops help text
the moment an error appears, which is when the reader most needs it). Rendering
the markup means owning the wiring that Django's own ``div.html`` renderer would
otherwise do.
"""

from __future__ import annotations

from django import template

from dashboard.services import editing

register = template.Library()


@register.filter
def is_controlled(bound_field) -> bool:
    """Whether editing this field asks Kiam to re-check something.

    Takes the bound field rather than a name so a template cannot silently ask
    about a field that is not on the form — a typo returns False either way, but
    `{{ form.full_nmae|is_controlled }}` renders nothing at all and is visible,
    where a string lookup would quietly answer "safe" about a controlled field.
    """
    name = getattr(bound_field, "name", None)
    if not name:
        return False
    return editing.is_controlled(name)


@register.filter
def described(bound_field):
    """Render the control wired to everything ``_field.html`` emits about it.

    This replaced a ``describe_fields()`` helper that set ``aria-describedby`` in
    the form's ``__init__``, and the Phase 6 accessibility review found three
    separate failures in that approach — all of which come from doing it too early
    and in the wrong place:

    * **Errors were never referenced.** They are not known at ``__init__``. Worse,
      setting the attribute at all made it *impossible* for Django to add them:
      ``BoundField.aria_describedby`` returns ``None`` when the widget already
      carries one, so the id was in the DOM, referenced by nothing. A screen-reader
      user tabbing to an invalid field heard the label, ``aria-invalid``, and no
      reason.
    * **It used ``id_for_label``, which is empty for grouped widgets.**
      ``CheckboxSelectMultiple`` and ``RadioSelect`` deliberately return ``""``
      (there is no single input for a group label to point at), so every one of the
      ten ``client_groups`` checkboxes was stamped with the literal string
      ``aria-describedby="-controlled"``. That is the field that decides whether an
      enhanced DBS is required, and its "this withdraws your badge" warning was
      visible to sighted users and absent for everyone else. ``auto_id`` is the
      right attribute and is never empty.
    * **The hyphen/underscore mismatch.** Forms that did not call the helper got
      Django's own ``{auto_id}_helptext`` while the template emitted
      ``{auto_id}-help`` — a dangling reference on the file input, the email-change
      field and every register-URL row.

    Doing it here means one definition, evaluated when the errors exist, matching
    the ids the same partial writes three lines away.
    """
    described_by = []
    if is_controlled(bound_field):
        described_by.append(f"{bound_field.auto_id}-controlled")
    if bound_field.field.help_text:
        described_by.append(f"{bound_field.auto_id}-help")
    if bound_field.errors:
        described_by.append(f"{bound_field.auto_id}-error")

    attrs = {}
    if described_by:
        attrs["aria-describedby"] = " ".join(described_by)
    if bound_field.errors:
        attrs["aria-invalid"] = "true"

    return bound_field.as_widget(attrs=attrs)
