"""Template helpers for the dashboard.

One filter, and it exists so the per-field "this one is checked" marker reads as
what it is at the point of use, rather than as a dictionary lookup the template
language cannot do without a second helper anyway.
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
