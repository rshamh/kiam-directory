"""Search-vector maintenance.

The M2M tests are the reason this file exists. Adding a speciality to a listing
changes no column on the practitioner row, so ``post_save`` never fires — and a
listing tagged "Adult ADHD assessment" whose vector has never heard of ADHD is
simply not findable, with no error and nothing in a log. That is the bug these
tests exist to keep fixed.
"""

from __future__ import annotations

import pytest
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.management import call_command
from django.db.models import F

from directory.factories import PractitionerFactory, SpecialityFactory
from directory.models import Practitioner
from directory.services import search_index

pytestmark = pytest.mark.django_db


def _matches(term: str):
    """Practitioners whose search vector matches `term`."""
    return Practitioner.objects.filter(search_vector=SearchQuery(term, config="english"))


def _rank(practitioner, term: str) -> float:
    row = (
        Practitioner.objects.filter(pk=practitioner.pk)
        .annotate(rank=SearchRank(F("search_vector"), SearchQuery(term, config="english")))
        .values_list("rank", flat=True)
        .first()
    )
    return row or 0.0


# ---------------------------------------------------------------------------
# The four bands
# ---------------------------------------------------------------------------


def test_a_name_is_indexed_on_save():
    practitioner = PractitionerFactory(full_name="Dr Farah Nasrallah")
    assert _matches("Nasrallah").filter(pk=practitioner.pk).exists()


def test_intro_and_services_are_indexed():
    practitioner = PractitionerFactory(
        intro="Supporting adults through burnout and workplace stress.",
        services="Evening appointments and written reports.",
    )

    assert _matches("burnout").filter(pk=practitioner.pk).exists()
    assert _matches("reports").filter(pk=practitioner.pk).exists()


def test_a_name_outranks_the_same_word_buried_in_services():
    """Band A over band D — someone searching a name means the name."""
    named = PractitionerFactory(full_name="Dr Aisha Bennett")
    mentioned = PractitionerFactory(
        full_name="Dr Someone Else", services="Formerly worked alongside Bennett."
    )

    assert _rank(named, "Bennett") > _rank(mentioned, "Bennett")


def test_an_empty_intro_does_not_wipe_the_index():
    """A SearchVector over a NULL column NULLs the whole concatenation."""
    practitioner = PractitionerFactory(full_name="Dr Priya Raman", intro="", services="")

    assert _matches("Raman").filter(pk=practitioner.pk).exists()


# ---------------------------------------------------------------------------
# The M2M problem
# ---------------------------------------------------------------------------


def test_adding_a_speciality_reindexes_without_touching_the_practitioner():
    """post_save cannot see this. m2m_changed is what makes it work."""
    practitioner = PractitionerFactory(full_name="Dr Test One")
    speciality = SpecialityFactory(name="Adult ADHD assessment")

    assert not _matches("ADHD").filter(pk=practitioner.pk).exists()

    practitioner.specialities.add(speciality)

    assert _matches("ADHD").filter(pk=practitioner.pk).exists()


def test_removing_a_speciality_reindexes():
    speciality = SpecialityFactory(name="Adult ADHD assessment")
    practitioner = PractitionerFactory(specialities=[speciality])
    assert _matches("ADHD").filter(pk=practitioner.pk).exists()

    practitioner.specialities.remove(speciality)

    assert not _matches("ADHD").filter(pk=practitioner.pk).exists()


def test_clearing_specialities_reindexes():
    speciality = SpecialityFactory(name="Adult ADHD assessment")
    practitioner = PractitionerFactory(specialities=[speciality])

    practitioner.specialities.clear()

    assert not _matches("ADHD").filter(pk=practitioner.pk).exists()


def test_adding_from_the_reverse_side_also_reindexes():
    """speciality.practitioners.add(...) — the direction that is easy to miss."""
    practitioner = PractitionerFactory(full_name="Dr Test Two")
    speciality = SpecialityFactory(name="Complex PTSD")

    speciality.practitioners.add(practitioner)

    assert _matches("PTSD").filter(pk=practitioner.pk).exists()


def test_a_set_call_reindexes():
    first = SpecialityFactory(name="Adult ADHD assessment")
    second = SpecialityFactory(name="Autism assessment")
    practitioner = PractitionerFactory(specialities=[first])

    practitioner.specialities.set([second])

    assert not _matches("ADHD").filter(pk=practitioner.pk).exists()
    assert _matches("Autism").filter(pk=practitioner.pk).exists()


# ---------------------------------------------------------------------------
# Synonyms
# ---------------------------------------------------------------------------


def test_synonyms_are_searchable():
    """The whole reason "add" finds ADHD."""
    speciality = SpecialityFactory(name="Adult ADHD assessment", synonyms=["adhd diagnosis", "add"])
    practitioner = PractitionerFactory(specialities=[speciality])

    assert _matches("add").filter(pk=practitioner.pk).exists()


def test_synonyms_are_never_shown_but_are_still_matched():
    speciality = SpecialityFactory(name="Autism assessment", synonyms=["asperger"])
    practitioner = PractitionerFactory(specialities=[speciality])

    assert _matches("asperger").filter(pk=practitioner.pk).exists()
    # The synonym is an index-only term; it is not part of any label.
    assert "asperger" not in speciality.name.lower()


def test_a_speciality_with_no_synonyms_is_fine():
    speciality = SpecialityFactory(name="Bereavement and grief", synonyms=[])
    practitioner = PractitionerFactory(specialities=[speciality])

    assert _matches("bereavement").filter(pk=practitioner.pk).exists()


# ---------------------------------------------------------------------------
# The nightly rebuild
# ---------------------------------------------------------------------------


def test_rebuild_all_repairs_a_vector_wiped_behind_the_signals():
    """queryset.update() emits no signal — this is what catches that."""
    practitioner = PractitionerFactory(full_name="Dr Wiped Vector")
    Practitioner.objects.filter(pk=practitioner.pk).update(search_vector=None)
    assert not _matches("Wiped").filter(pk=practitioner.pk).exists()

    search_index.rebuild_all()

    assert _matches("Wiped").filter(pk=practitioner.pk).exists()


def test_the_rebuild_command_runs():
    practitioner = PractitionerFactory(full_name="Dr Command Test")
    Practitioner.objects.filter(pk=practitioner.pk).update(search_vector=None)

    call_command("rebuild_search_index", verbosity=0)

    assert _matches("Command").filter(pk=practitioner.pk).exists()


def test_rebuild_all_picks_up_a_synonym_added_to_the_taxonomy():
    """The case the signals genuinely cannot catch.

    Editing a Speciality changes the Speciality row, not the Practitioner — so
    nothing signals, and every listing carrying it has a stale vector until the
    nightly rebuild. This is why that job exists.
    """
    speciality = SpecialityFactory(name="Chronic fatigue", synonyms=[])
    practitioner = PractitionerFactory(specialities=[speciality])
    assert not _matches("myalgic").filter(pk=practitioner.pk).exists()

    speciality.synonyms = ["myalgic encephalomyelitis"]
    speciality.save(update_fields=["synonyms"])
    # Still stale — nothing has signalled.
    assert not _matches("myalgic").filter(pk=practitioner.pk).exists()

    search_index.rebuild_all()

    assert _matches("myalgic").filter(pk=practitioner.pk).exists()


def test_published_only_limits_the_rebuild():
    published = PractitionerFactory(published=True, full_name="Dr Published Person")
    draft = PractitionerFactory(full_name="Dr Draft Person")
    Practitioner.objects.update(search_vector=None)

    call_command("rebuild_search_index", "--published-only", verbosity=0)

    assert _matches("Published").filter(pk=published.pk).exists()
    assert not _matches("Draft").filter(pk=draft.pk).exists()
