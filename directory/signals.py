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

from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from .models import Practitioner, SlugRedirect
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


# ===========================================================================
# Phase 3 additions — slug redirects
# ===========================================================================
#
# A published profile's URL is the one thing about a listing that other people
# own copies of: a practitioner's own website links to it, a referral email
# quotes it, a search engine has indexed it. So the slug is treated as immutable
# once a listing has been live, and a change that happens anyway — a corrected
# spelling, a name change after marriage — leaves a redirect behind instead of
# breaking every one of those links.
#
# Two receivers rather than one, because neither half can do it alone. pre_save
# is the last moment the old slug is still readable; post_save is the first
# moment we know the new one actually committed. Writing the redirect row from
# pre_save would leave one behind for a save that then failed.


@receiver(pre_save, sender=Practitioner, dispatch_uid="directory.capture_previous_slug")
def capture_previous_slug(sender, instance, **kwargs):
    """Stash the slug currently in the database, if it differs."""
    instance._previous_slug = None

    if instance.pk is None:
        return

    previous = Practitioner.objects.filter(pk=instance.pk).values_list("slug", flat=True).first()
    if previous and previous != instance.slug:
        instance._previous_slug = previous


@receiver(post_save, sender=Practitioner, dispatch_uid="directory.write_slug_redirect")
def write_slug_redirect(sender, instance, **kwargs):
    """Preserve the old URL, but only for a listing that has been public.

    ``published_at`` rather than the current status: a suspended listing's old
    URL is still out in the world, and a listing that has never been published
    has no inbound links worth a redirect row.
    """
    previous = getattr(instance, "_previous_slug", None)
    instance._previous_slug = None

    if not previous or instance.published_at is None:
        return

    # A slug that comes back into use (A -> B -> A) must not leave a redirect row
    # pointing at the address it now *is*. Live slugs win in
    # profile.resolve(), so this cannot loop — but a stale row would occupy the
    # unique old_slug and block a genuine redirect later.
    SlugRedirect.objects.filter(old_slug=instance.slug).delete()

    SlugRedirect.objects.update_or_create(old_slug=previous, defaults={"practitioner": instance})
