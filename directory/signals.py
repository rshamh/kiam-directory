"""Signals that keep the search index current.

TWO receivers, and the second is the one that matters.

``post_save`` on ``Practitioner`` catches edits to name, intro and services.
It does **not** fire when a ManyToMany changes — adding "Adult ADHD assessment"
to a listing changes no column on the practitioner row, so Django has nothing to
signal about. Relying on ``post_save`` alone produces a listing whose search
vector has never heard of the speciality it is tagged with: not findable, no
error, nothing in a log. ``m2m_changed`` on ``Practitioner.specialities`` is the
half that fixes it.

Both are guarded against recursion: ``search_index.rebuild()`` writes through a
queryset ``.update()``, which does not emit ``post_save``.
"""

from __future__ import annotations

from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver

from .models import Practitioner
from .services import search_index


@receiver(post_save, sender=Practitioner, dispatch_uid="directory.rebuild_search_vector")
def rebuild_on_save(sender, instance, **kwargs):
    """Name, intro and services live on the row, so post_save sees them."""
    search_index.rebuild(instance)


@receiver(
    m2m_changed,
    sender=Practitioner.specialities.through,
    dispatch_uid="directory.rebuild_search_vector_m2m",
)
def rebuild_on_speciality_change(sender, instance, action, reverse, **kwargs):
    """The half post_save cannot do. See the module docstring.

    Only the *_after* actions: on ``pre_add`` the join rows are not written yet,
    so a rebuild would index the state before the change and undo itself.

    ``reverse=True`` means someone did ``speciality.practitioners.add(...)``, and
    then ``instance`` is the Speciality, not the Practitioner — so the affected
    practitioners are resolved from the pk set instead.
    """
    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    if not reverse:
        search_index.rebuild(instance)
        return

    pks = kwargs.get("pk_set") or []
    for practitioner in Practitioner.objects.filter(pk__in=pks).prefetch_related("specialities"):
        search_index.rebuild(practitioner)
