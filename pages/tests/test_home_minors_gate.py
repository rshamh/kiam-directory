"""The home-page grid does not offer under-18 work that nobody has cleared.

The third file of its kind, after ``directory/tests/test_profile_minor_gate.py``
(Phase 3) and ``directory/tests/test_search_minors_gate.py`` (Phase 4). Phase 5
shipped a third public listing surface with no equivalent, which the compliance
review caught.

**What this file is NOT.** It is not a fourth copy of the under-18 gate.
``directory/services/profile.py`` records the standing decision that adding a
speciality gate at one surface would recreate the one-sided leak CLAUDE.md warns
about, and that reasoning holds. The two real gates —
``Practitioner.can_show_minor_groups`` and the ``minor_work_status=CLEARED`` filter
in the search queryset — answer "may this listing be shown to somebody asking about
children?", and both still do, untouched.

This file covers a different question, which only the home page raises: **should
Kiam CHOOSE this listing, unprompted, for the twelve faces on its own front page?**
On ``/search/`` an un-gated ``implies_minors`` speciality reaches a visitor only
because they typed something or ticked a facet. In the grid, Kiam does the
selecting, publishes the pill under its own claim to have checked the listing, and
nobody asked. Excluding it from a sample of twelve out of twenty-eight hides
nothing — the profile is unchanged, the listing stays searchable — so there is no
asymmetry here for a later change to break.

The underlying defect is still CLAUDE.md open question 1: ``recompute()`` derives
``works_with_minors`` from client groups alone, so a speciality that implies child
work requires no DBS and the listing renders ``NOT_APPLICABLE`` with a full badge.
That is **Dr. Abbass / CQC compliance lead**. Phase 5 raises its priority; it does
not settle it.
"""

from __future__ import annotations

import pytest

from directory.factories import (
    ClientGroupFactory,
    PractitionerFactory,
    SpecialityCategoryFactory,
    SpecialityFactory,
)
from directory.models import MinorWorkStatus, Practitioner
from directory.services import search as search_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def child_speciality():
    """A speciality that is unambiguously child work."""
    return SpecialityFactory(
        slug="child-adhd-assessment",
        name="Child & adolescent ADHD assessment",
        category=SpecialityCategoryFactory(slug="neuro", name="Neurodevelopmental"),
        implies_minors=True,
    )


@pytest.fixture
def adult_speciality():
    return SpecialityFactory(
        slug="adult-adhd-assessment",
        name="Adult ADHD assessment",
        category=SpecialityCategoryFactory(slug="neuro", name="Neurodevelopmental"),
        implies_minors=False,
    )


def in_grid(size=12):
    return {p.pk for p in search_service.homepage_grid(size)}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def test_a_provisional_dbs_keeps_a_child_speciality_off_the_front_page(child_speciality):
    """PROVISIONAL is live for ADULT work only. A grid card advertising a child
    assessment is not adult work, whatever the client groups say."""
    practitioner = PractitionerFactory(
        published=True,
        slug="provisional-child-work",
        specialities=[child_speciality],
        provisional_dbs=True,
        completeness=90,
    )
    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL

    assert practitioner.pk not in in_grid()


def test_a_blocked_dbs_keeps_a_child_speciality_off_the_front_page(child_speciality):
    """BLOCKED is not "pending": the window elapsed, the check was rejected, or the
    certificate expired. It was on the live grid on five of six days simulated."""
    practitioner = PractitionerFactory(
        published=True,
        slug="blocked-child-work",
        specialities=[child_speciality],
        provisional_dbs=True,
        completeness=90,
    )
    Practitioner.objects.filter(pk=practitioner.pk).update(minor_work_status=MinorWorkStatus.BLOCKED)

    assert practitioner.pk not in in_grid()


def test_the_open_question_case_is_excluded_too(child_speciality):
    """The one that matters most, and the reason ``NOT_APPLICABLE`` is in the list.

    A listing that tags a child speciality but selects only ADULT client groups is
    ``NOT_APPLICABLE``: ``recompute()`` derives the DBS requirement from client
    groups alone, so **no DBS is ever required** and the badge is full. Nothing about
    that listing looks wrong, which is exactly why the grid must not pick it.
    """
    practitioner = PractitionerFactory(
        published=True,
        slug="no-dbs-required-at-all",
        specialities=[child_speciality],
        client_groups=[ClientGroupFactory(slug="adults", name="Adults", is_minors=False)],
        verified=True,
        completeness=90,
    )
    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.NOT_APPLICABLE
    assert practitioner.is_verified, "the badge is full, which is what makes this hard to spot"

    assert practitioner.pk not in in_grid()


def test_a_cleared_dbs_is_still_eligible(child_speciality):
    """The gate must not become "no child work on the home page". A cleared enhanced
    DBS is exactly what the badge is for."""
    practitioner = PractitionerFactory(
        published=True,
        slug="cleared-child-work",
        specialities=[child_speciality],
        dbs_cleared=True,
        completeness=90,
    )
    practitioner.refresh_from_db()
    assert practitioner.minor_work_status == MinorWorkStatus.CLEARED

    assert practitioner.pk in in_grid()


def test_an_adult_only_listing_is_untouched(adult_speciality):
    """No DBS, no child speciality, no exclusion — otherwise the gate would empty the
    grid of the practitioners it is mostly made of."""
    practitioner = PractitionerFactory(
        published=True,
        slug="adults-only",
        specialities=[adult_speciality],
        completeness=90,
    )

    assert practitioner.pk in in_grid()


# ---------------------------------------------------------------------------
# The rendered page
# ---------------------------------------------------------------------------


def test_no_child_speciality_pill_reaches_the_home_page_without_a_cleared_dbs(
    client, child_speciality, adult_speciality
):
    """Asserted on the HTML, not on the queryset.

    The Phase 3 gate's lesson: a gate that filters the right rows and a template that
    prints the wrong string are the same bug to a reader. This is the string that was
    live on the home page when the review ran.
    """
    PractitionerFactory(
        published=True,
        slug="provisional-child-work",
        full_name="Provisional Practitioner",
        specialities=[child_speciality],
        provisional_dbs=True,
        completeness=90,
    )
    PractitionerFactory(
        published=True,
        slug="adults-only",
        full_name="Adults Only Practitioner",
        specialities=[adult_speciality],
        completeness=90,
    )

    body = client.get("/").content.decode()

    assert "Child &amp; adolescent ADHD assessment" not in body
    assert "Child & adolescent ADHD assessment" not in body
    assert "Provisional Practitioner" not in body
    # And the adult listing is still there, so this is not an empty-grid pass.
    assert "Adults Only Practitioner" in body


def test_the_two_real_gates_are_still_in_the_source(client):
    """The grep the Phase 4 gate test ends with, extended to three call sites.

    Deleting any one of them leaves the other two's tests green, so the assertion has
    to be about the source rather than about behaviour. The grid clause is included
    because it is the one a future reader is most likely to mistake for redundancy.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    models = (root / "directory" / "models.py").read_text()
    assert "def can_show_minor_groups" in models
    assert "def visible_client_groups" in models

    search = (root / "directory" / "services" / "search.py").read_text()
    assert "minor_work_status=MinorWorkStatus.CLEARED" in search, "the search gate is gone"
    assert "_ungated_minor_work_ids" in search, "the home-grid exclusion is gone"
    assert "_ungated_minor_work_ids()" in search, "the exclusion is defined but not called"

    profile = (root / "directory" / "services" / "profile.py").read_text()
    assert "visible_client_groups" in profile
