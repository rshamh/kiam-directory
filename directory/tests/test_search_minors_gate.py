"""The under-18 gate on the SEARCH QUERYSET — the second half of the two-place gate.

CLAUDE.md: the gate is applied in two places, `Practitioner.can_show_minor_groups`
for the profile and `minor_work_status=CLEARED` in the search queryset, and
**either alone leaks**. Phase 3 built and tested the profile half; this is the
other one, and it is the higher-severity of the two — a profile leak needs
somebody to already be looking at that practitioner, whereas a search leak
actively hands a `PROVISIONAL` practitioner to a parent who filtered for a child
therapist.

In its own file, like the Phase 3 gate test, so it cannot get lost in a module
about ranking.

Asserted through the queryset AND through the rendered page, because those are two
different ways to get it wrong: the filter could be missing, or it could be right
and the template could render from something else.
"""

from __future__ import annotations

import pytest

from directory.factories import ClientGroupFactory, PractitionerFactory
from directory.models import MinorWorkStatus
from directory.services import search, verification

pytestmark = pytest.mark.django_db

ADULTS = "Adults (18 and over)"
MINORS = "Adolescents (12 to 17)"


@pytest.fixture
def groups():
    return (
        ClientGroupFactory(slug="adults", name=ADULTS, min_age=18, is_minors=False),
        ClientGroupFactory(slug="adolescents", name=MINORS, min_age=12, max_age=17, is_minors=True),
    )


@pytest.fixture
def cohort(groups):
    """One practitioner per DBS state, all tagged with the same minors group."""
    adults, minors = groups
    cleared = PractitionerFactory(
        published=True,
        slug="cleared-example",
        full_name="Cleared Example",
        client_groups=[adults, minors],
        dbs_cleared=True,
    )
    provisional = PractitionerFactory(
        published=True,
        slug="provisional-example",
        full_name="Provisional Example",
        client_groups=[adults, minors],
        provisional_dbs=True,
    )
    blocked = PractitionerFactory(
        published=True,
        slug="blocked-example",
        full_name="Blocked Example",
        client_groups=[adults, minors],
    )
    verification.recompute(blocked)
    blocked.refresh_from_db()

    assert cleared.minor_work_status == MinorWorkStatus.CLEARED
    assert provisional.minor_work_status == MinorWorkStatus.PROVISIONAL
    assert blocked.minor_work_status == MinorWorkStatus.BLOCKED
    return cleared, provisional, blocked


def _slugs(params):
    return {p.slug for p in search.results_page(params).items}


# ---------------------------------------------------------------------------
# The gate, on the queryset
# ---------------------------------------------------------------------------


def test_filtering_to_a_minors_group_returns_only_cleared(cohort):
    """THE Phase 4 gate."""
    cleared, provisional, blocked = cohort

    found = _slugs(search.SearchParams(client_groups=["adolescents"]))

    assert found == {cleared.slug}
    assert provisional.slug not in found
    assert blocked.slug not in found


def test_a_provisional_practitioner_still_appears_in_an_unfiltered_search(cohort):
    """PROVISIONAL is live for ADULT work, not suppressed altogether.

    A gate that hid them entirely would be a different bug — the listing is
    legitimately published and findable; it is the under-18 scope that is not.
    """
    cleared, provisional, blocked = cohort

    found = _slugs(search.SearchParams())

    assert {cleared.slug, provisional.slug, blocked.slug} <= found


def test_a_provisional_practitioner_appears_when_filtering_to_adults(cohort):
    cleared, provisional, blocked = cohort

    found = _slugs(search.SearchParams(client_groups=["adults"]))

    assert provisional.slug in found
    assert cleared.slug in found


def test_mixing_an_adult_and_a_minors_group_still_gates(cohort):
    """The trap: `group=adults&group=adolescents` is an OR over client groups, so
    a PROVISIONAL practitioner matches the adults half. Asking about under-18s at
    all is what triggers the gate, not asking about them exclusively."""
    cleared, provisional, blocked = cohort

    found = _slugs(search.SearchParams(client_groups=["adults", "adolescents"]))

    assert found == {cleared.slug}


def test_the_gate_survives_other_filters(cohort):
    """A leak through a narrower search is still a leak."""
    cleared, provisional, blocked = cohort

    found = _slugs(
        search.SearchParams(client_groups=["adolescents"], accepting_new_clients=True, verified_only=False)
    )

    assert provisional.slug not in found


# ---------------------------------------------------------------------------
# The gate, on the rendered page
# ---------------------------------------------------------------------------


def test_a_provisional_practitioner_is_absent_from_rendered_search_results(client, cohort):
    cleared, provisional, blocked = cohort

    body = client.get("/search/?group=adolescents").content.decode()

    assert cleared.full_name in body
    assert provisional.full_name not in body
    assert provisional.slug not in body


def test_the_gate_holds_on_the_htmx_partial_too(client, cohort):
    """The fragment is the same queryset, but "the same queryset" is an assumption
    until something checks it — an HTMX path that bypassed the gate would leak to
    everyone with JavaScript, which is almost everyone."""
    cleared, provisional, blocked = cohort

    body = client.get("/search/?group=adolescents", HTTP_HX_REQUEST="true").content.decode()

    assert cleared.full_name in body
    assert provisional.full_name not in body


# ---------------------------------------------------------------------------
# Both halves exist
# ---------------------------------------------------------------------------


def test_both_halves_of_the_gate_are_present_in_the_source():
    """A grep, deliberately — the two halves have to stay together.

    CLAUDE.md: "Any change to one requires a matching change and test in the
    other." Deleting either filter would leave the other's tests green, so this
    asserts both call sites still exist.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]

    search_source = (root / "directory" / "services" / "search.py").read_text()
    assert "minor_work_status=MinorWorkStatus.CLEARED" in search_source, (
        "the search-queryset half of the under-18 gate has gone"
    )

    model_source = (root / "directory" / "models.py").read_text()
    assert "def can_show_minor_groups" in model_source
    assert "def visible_client_groups" in model_source

    profile_source = (root / "directory" / "services" / "profile.py").read_text()
    assert "visible_client_groups()" in profile_source, "the profile half of the under-18 gate has gone"


# ---------------------------------------------------------------------------
# The implies_minors speciality path — added at the Phase 4 compliance review
# ---------------------------------------------------------------------------
#
# A fail-safe interim, not the decision. Whether a speciality TAG creates a DBS
# requirement is Dr. Abbass's and the CQC lead's call; until that is recorded,
# search declines the risky side of it. See `search._requests_minor_work`.


@pytest.fixture
def child_speciality():
    from directory.factories import SpecialityFactory

    return SpecialityFactory(
        slug="child-adhd-assessment",
        name="Child & adolescent ADHD assessment",
        implies_minors=True,
    )


def test_a_child_speciality_search_returns_only_cleared_practitioners(cohort, child_speciality):
    """The path the review found, and the reason it is worse than the Phase 3 flag.

    A PROVISIONAL practitioner's minor client group is correctly hidden by BOTH
    halves of the gate — and search then handed the person over anyway, with the
    child speciality printed on their card, in answer to an explicitly child-focused
    request. A parent ticking a box labelled "Child & adolescent ADHD assessment"
    cannot see the internal ClientGroup/Speciality distinction.
    """
    cleared, provisional, blocked = cohort
    for practitioner in cohort:
        practitioner.specialities.add(child_speciality)

    found = _slugs(search.SearchParams(specialities=["child-adhd-assessment"]))

    assert found == {cleared.slug}


def test_an_adult_speciality_search_is_not_gated(cohort):
    """Over-restriction is the safe direction, but it must not be the default."""
    from directory.factories import SpecialityFactory

    adult = SpecialityFactory(slug="adult-adhd", name="Adult ADHD assessment", implies_minors=False)
    for practitioner in cohort:
        practitioner.specialities.add(adult)

    found = _slugs(search.SearchParams(specialities=["adult-adhd"]))

    assert len(found) == 3


def test_a_category_containing_a_child_speciality_is_not_gated(cohort, child_speciality):
    """Deliberately NOT gated: "Neurodevelopmental" contains a child speciality, so
    gating the category would hide every adult ADHD practitioner without a DBS."""
    cleared, provisional, blocked = cohort
    for practitioner in cohort:
        practitioner.specialities.add(child_speciality)

    found = _slugs(search.SearchParams(speciality_categories=[child_speciality.category.slug]))

    assert len(found) == 3


def test_the_child_speciality_gate_holds_on_the_rendered_page(client, cohort, child_speciality):
    cleared, provisional, blocked = cohort
    for practitioner in cohort:
        practitioner.specialities.add(child_speciality)

    body = client.get("/search/?speciality=child-adhd-assessment").content.decode()

    assert cleared.full_name in body
    assert provisional.full_name not in body
