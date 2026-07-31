"""Signals that keep the derived columns current.

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
from .services import completeness, search_index


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


# ===========================================================================
# Phase 6 additions — the completeness score
# ===========================================================================
#
# `Practitioner.completeness` has been a ranking input since Phase 1 and the
# home-page grid's gate since Phase 5, and NOTHING WROTE IT. Every real listing
# would have sat at the default 0 — bottom of every ranking tie-break, and absent
# from the front page, which requires 70. `seed_demo` hard-codes plausible values
# and the test factory defaults to 80, which is precisely why a green suite and a
# populated development database both looked right.
#
# Same two receivers as the search vector, for the same reason: an M2M change
# writes no column on the practitioner row, so `post_save` alone would leave a
# listing that has just chosen six specialities still scored as though it had
# none. `completeness.recompute()` writes through `.update()`, so neither
# receiver can recurse.
#
# The M2M receiver covers FOUR relations, not one. The search vector only cares
# about specialities; the score also counts approaches, client groups and
# languages, and a receiver registered for one `through` model hears nothing
# about the others.


@receiver(post_save, sender=Practitioner, dispatch_uid="directory.recompute_completeness")
def recompute_completeness_on_save(sender, instance, **kwargs):
    completeness.recompute(instance)


def _recompute_completeness_m2m(instance, action, reverse, pk_set):
    """Shared body for the four taxonomy relations.

    Only the `post_*` actions: on `pre_add` the join rows are not written yet, so
    the score would be computed against the state before the change and would
    undo itself.

    `reverse=True` means somebody did `speciality.practitioners.add(...)`, so
    `instance` is the term and the affected practitioners come from `pk_set`.
    """
    if action not in {"post_add", "post_remove", "post_clear"}:
        return

    if not reverse:
        completeness.recompute(instance)
        return

    for practitioner in Practitioner.objects.filter(pk__in=pk_set or []):
        completeness.recompute(practitioner)


for _relation in (
    Practitioner.specialities,
    Practitioner.approaches,
    Practitioner.client_groups,
    Practitioner.languages,
):
    receiver(
        m2m_changed,
        sender=_relation.through,
        dispatch_uid=f"directory.recompute_completeness_{_relation.field.name}",
    )(
        lambda sender, instance, action, reverse, **kwargs: _recompute_completeness_m2m(
            instance, action, reverse, kwargs.get("pk_set")
        )
    )

del _relation
