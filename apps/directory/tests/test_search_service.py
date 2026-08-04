"""The search queryset and the ranking.

`directory/services/search.py` is adopted from elsewhere, so these tests split in
two: the ones that pin the AUTHORED behaviour (weights, bands, the seeded shuffle,
the online-or-in-radius rule) and the ones that pin the five things that had to
change during adoption. The second group matters more — each is a bug the file
shipped with, and each would come back silently if someone "simplified" the fix.
"""

from __future__ import annotations

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.directory.factories import (
    ApproachFactory,
    ClientGroupFactory,
    FundingOptionFactory,
    LanguageFactory,
    PractitionerFactory,
    PractitionerLocationFactory,
    SpecialityCategoryFactory,
    SpecialityFactory,
)
from apps.directory.models import MinorWorkStatus, Practitioner, PublicationStatus
from apps.directory.services import search

pytestmark = pytest.mark.django_db

EPSOM = (-0.2674, 51.3360)
GUILDFORD = (-0.5704, 51.2362)
EDINBURGH = (-3.1883, 55.9533)


def _slugs(params):
    return [p.slug for p in search.results_page(params).items]


def _at(practitioner, coords, **kwargs):
    return PractitionerLocationFactory(practitioner=practitioner, geo=Point(*coords, srid=4326), **kwargs)


# ---------------------------------------------------------------------------
# Weights — one edit and one test (the file's own promise)
# ---------------------------------------------------------------------------


def test_the_weights_sum_to_one():
    """The module docstring promises this. Tuning that breaks it silently rescales
    every score and makes the band width mean something different."""
    assert sum(search.weights().values()) == pytest.approx(1.0)


def test_every_weight_is_a_module_constant():
    """ "Weights stay module constants so tuning is one edit and one test."""
    for name in ("W_PROXIMITY", "W_TEXT", "W_COMPLETENESS", "W_VERIFIED", "W_ACCEPTING", "W_RECENT"):
        assert isinstance(getattr(search, name), float)


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.DRAFT,
        PublicationStatus.SUBMITTED,
        PublicationStatus.APPROVED,
        PublicationStatus.SUSPENDED,
        PublicationStatus.UNPUBLISHED,
        PublicationStatus.REMOVED,
    ],
)
def test_only_published_listings_are_searchable(status):
    practitioner = PractitionerFactory(published=True, slug="hidden")
    Practitioner.objects.filter(pk=practitioner.pk).update(status=status)

    assert _slugs(search.SearchParams()) == []


# ---------------------------------------------------------------------------
# Location: the authored rule
# ---------------------------------------------------------------------------


def test_a_practitioner_matches_if_any_location_is_in_radius():
    """The reason PractitionerLocation is a FK collection."""
    practitioner = PractitionerFactory(published=True, slug="two-sites", in_person_only=True)
    _at(practitioner, GUILDFORD)
    _at(practitioner, EPSOM, is_primary=False)

    found = _slugs(search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=3))

    assert found == ["two-sites"]


def test_an_online_practitioner_matches_outside_the_radius():
    PractitionerFactory(published=True, slug="online", online_only=True)

    assert _slugs(search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=1)) == ["online"]


def test_an_online_practitioner_is_excluded_when_online_is_not_allowed():
    PractitionerFactory(published=True, slug="online", online_only=True)

    params = search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=1, include_online=False)

    assert _slugs(params) == []


def test_a_distant_in_person_practitioner_does_not_match():
    practitioner = PractitionerFactory(published=True, slug="far", in_person_only=True)
    _at(practitioner, EDINBURGH)

    assert _slugs(search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=50)) == []


# ---------------------------------------------------------------------------
# ADOPTION FIX 1 — accessibility and radius must be the SAME address
# ---------------------------------------------------------------------------


def test_a_step_free_office_elsewhere_does_not_satisfy_a_local_search():
    """The bug the adopted file shipped with, and the reason for `_location_filter`.

    Two separate `.filter()` calls on `locations` resolve against two separate
    joins, so this practitioner — whose in-range office has steps and whose
    step-free office is 20 miles away — passed both "any location in range" and
    "any location step-free" while having nowhere that is both. Somebody who needs
    step-free access would have been sent to a building with steps.
    """
    practitioner = PractitionerFactory(published=True, slug="mismatched", in_person_only=True)
    _at(practitioner, EPSOM, step_free_access=False)
    _at(practitioner, GUILDFORD, step_free_access=True, is_primary=False)

    params = search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=3, step_free_access=True)

    assert _slugs(params) == []


def test_one_address_that_is_both_in_range_and_step_free_does_match():
    """The other half — the fix must not simply exclude everyone."""
    practitioner = PractitionerFactory(published=True, slug="matched", in_person_only=True)
    _at(practitioner, EPSOM, step_free_access=True)

    params = search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=3, step_free_access=True)

    assert _slugs(params) == ["matched"]


def test_several_access_requirements_must_all_hold_at_one_address():
    both = PractitionerFactory(published=True, slug="both", in_person_only=True)
    _at(both, EPSOM, step_free_access=True, hearing_loop=True)

    split = PractitionerFactory(published=True, slug="split", in_person_only=True)
    _at(split, EPSOM, step_free_access=True, hearing_loop=False)
    _at(split, EPSOM, step_free_access=False, hearing_loop=True, is_primary=False)

    params = search.SearchParams(step_free_access=True, hearing_loop=True)

    assert _slugs(params) == ["both"]


def test_an_access_requirement_excludes_online_only_listings():
    """ "Step-free access" cannot describe a video call, so asking for one asks for
    a building — and the "…or works online" fallback has to switch off, or the
    filter returns listings that cannot possibly satisfy it."""
    PractitionerFactory(published=True, slug="online", online_only=True)
    local = PractitionerFactory(published=True, slug="local")
    _at(local, EPSOM, step_free_access=True)

    params = search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=3, step_free_access=True)

    assert _slugs(params) == ["local"]


# ---------------------------------------------------------------------------
# ADOPTION FIX 2 — in_person_only used to filter nothing without a location
# ---------------------------------------------------------------------------


def test_in_person_only_excludes_online_listings_with_no_location_given():
    PractitionerFactory(published=True, slug="online", online_only=True)
    local = PractitionerFactory(published=True, slug="local")
    _at(local, EPSOM)

    assert _slugs(search.SearchParams(in_person_only=True)) == ["local"]


def test_online_only_excludes_practitioners_who_do_not_offer_it():
    PractitionerFactory(published=True, slug="in-person", in_person_only=True)
    PractitionerFactory(published=True, slug="remote", online_only=True)

    assert _slugs(search.SearchParams(online_only=True)) == ["remote"]


# ---------------------------------------------------------------------------
# ADOPTION FIX 5 — the two-level speciality tree
# ---------------------------------------------------------------------------


def test_a_category_matches_every_speciality_in_it():
    category = SpecialityCategoryFactory(slug="neuro", name="Neurodevelopmental")
    adhd = SpecialityFactory(slug="adhd", name="Adult ADHD assessment", category=category)
    other = SpecialityFactory(slug="grief", name="Grief")

    PractitionerFactory(published=True, slug="has-adhd", specialities=[adhd])
    PractitionerFactory(published=True, slug="has-grief", specialities=[other])

    assert _slugs(search.SearchParams(speciality_categories=["neuro"])) == ["has-adhd"]


def test_a_category_and_a_speciality_widen_rather_than_intersect():
    """OR, not AND. Ticking "anything in Neurodevelopmental" and then also ticking
    "Grief" asks for both, and an AND would return nothing at all."""
    category = SpecialityCategoryFactory(slug="neuro", name="Neurodevelopmental")
    adhd = SpecialityFactory(slug="adhd", name="Adult ADHD assessment", category=category)
    grief = SpecialityFactory(slug="grief", name="Grief")

    PractitionerFactory(published=True, slug="has-adhd", specialities=[adhd])
    PractitionerFactory(published=True, slug="has-grief", specialities=[grief])

    found = _slugs(search.SearchParams(speciality_categories=["neuro"], specialities=["grief"]))

    assert set(found) == {"has-adhd", "has-grief"}


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------


def test_facets_narrow_the_results():
    approach = ApproachFactory(slug="cbt", name="CBT")
    language = LanguageFactory(code="ur", name="Urdu")

    match = PractitionerFactory(
        published=True,
        slug="match",
        approaches=[approach],
        languages=[language],
        accepting_new_clients=True,
        is_prescriber=True,
        years_experience=12,
        fee_min=9000,
        typical_wait="short",
        evening_appointments=True,
        weekend_appointments=True,
        gender="female",
    )
    PractitionerFactory(published=True, slug="other", accepting_new_clients=False)

    params = search.SearchParams(
        approaches=["cbt"],
        languages=["ur"],
        accepting_new_clients=True,
        is_prescriber=True,
        min_years_experience=10,
        fee_max_pence=10000,
        max_wait="short",
        evening=True,
        weekend=True,
        genders=["female"],
    )

    assert _slugs(params) == [match.slug]


def test_the_wait_filter_includes_everything_shorter():
    """ "No longer than 2–4 weeks" has to mean "or sooner"."""
    PractitionerFactory(published=True, slug="immediate", typical_wait="immediate")
    PractitionerFactory(published=True, slug="medium", typical_wait="medium")
    PractitionerFactory(published=True, slug="long", typical_wait="long")

    found = set(_slugs(search.SearchParams(max_wait="medium")))

    assert found == {"immediate", "medium"}


def test_a_fee_filter_keeps_practitioners_who_publish_no_fee():
    """Excluding them would hide everyone who has not filled that field in, which
    is a data-completeness problem being presented as a price signal."""
    PractitionerFactory(published=True, slug="cheap", fee_min=5000)
    PractitionerFactory(published=True, slug="unpriced", fee_min=None)
    PractitionerFactory(published=True, slug="dear", fee_min=40000)

    found = set(_slugs(search.SearchParams(fee_max_pence=10000)))

    assert found == {"cheap", "unpriced"}


def test_verified_only_excludes_unverified_listings():
    PractitionerFactory(published=True, slug="verified", verified=True)
    PractitionerFactory(published=True, slug="plain")

    assert _slugs(search.SearchParams(verified_only=True)) == ["verified"]


def test_duplicate_matches_are_not_returned_twice():
    """Two matching specialities is one practitioner."""
    a = SpecialityFactory(slug="a", name="A")
    b = SpecialityFactory(slug="b", name="B")
    PractitionerFactory(published=True, slug="both", specialities=[a, b])

    assert _slugs(search.SearchParams(specialities=["a", "b"])) == ["both"]


# ---------------------------------------------------------------------------
# Ranking, the banded shuffle and the seed
# ---------------------------------------------------------------------------


def _cohort(n=12):
    """Practitioners identical in every ranking input, so only the shuffle differs."""
    return [
        PractitionerFactory(
            published=True,
            slug=f"p{i:02d}",
            completeness=80,
            accepting_new_clients=True,
            last_active_at=timezone.now(),
        )
        for i in range(n)
    ]


def test_the_same_seed_gives_the_same_order():
    _cohort()

    first = _slugs(search.SearchParams(seed=4242, page_size=50))
    second = _slugs(search.SearchParams(seed=4242, page_size=50))

    assert first == second
    assert len(first) == 12


def test_a_different_seed_reorders_within_a_band():
    """All twelve score identically, so they are in one band and the seed is the
    only thing deciding order. If two seeds agree the shuffle is not shuffling."""
    _cohort()

    a = _slugs(search.SearchParams(seed=1, page_size=50))
    b = _slugs(search.SearchParams(seed=999999, page_size=50))

    assert sorted(a) == sorted(b)
    assert a != b


def test_a_higher_score_outranks_the_shuffle():
    """The shuffle is WITHIN bands. A verified, complete, accepting listing must
    not be shuffled below an empty one — that would be a lottery, not ranking."""
    PractitionerFactory(
        published=True,
        slug="strong",
        verified=True,
        completeness=100,
        accepting_new_clients=True,
        last_active_at=timezone.now(),
    )
    for i in range(8):
        PractitionerFactory(published=True, slug=f"weak{i}", completeness=0, accepting_new_clients=False)

    assert _slugs(search.SearchParams(seed=7, page_size=50))[0] == "strong"


def test_text_relevance_ranks_a_name_match_first():
    from apps.directory.services import search_index

    PractitionerFactory(published=True, slug="unrelated", full_name="Grace Fielding")
    wanted = PractitionerFactory(published=True, slug="wanted", full_name="Marcus Trevelyan")
    for p in Practitioner.objects.all():
        search_index.rebuild(p)

    found = _slugs(search.SearchParams(q="Trevelyan", page_size=50))

    assert found[0] == wanted.slug


# ---------------------------------------------------------------------------
# ADOPTION FIX 4 — the featured cap
# ---------------------------------------------------------------------------


def _featured(slug, **kwargs):
    return PractitionerFactory(
        published=True,
        slug=slug,
        featured_until=timezone.now() + timezone.timedelta(days=30),
        **kwargs,
    )


def test_nothing_is_featured_yet_and_pagination_is_ordinary():
    _cohort(5)

    page = search.results_page(search.SearchParams(page_size=2))

    assert page.featured_total == 0
    assert len(page.items) == 2
    assert page.total == 5
    assert page.num_pages == 3


def test_featured_listings_come_first():
    _cohort(3)
    _featured("paid", completeness=0, accepting_new_clients=False)

    items = search.results_page(search.SearchParams(page_size=10)).items

    assert items[0].slug == "paid"


def test_no_more_than_the_cap_is_featured_on_one_page():
    """Paid placement is capped or it is a doorway (content-compliance §6).

    Six featured listings must not take the top six slots — an ORDER BY alone puts
    all six there, which is exactly what FEATURED_CAP_PER_PAGE existed (unused) to
    prevent.
    """
    _cohort(20)
    for i in range(6):
        _featured(f"paid{i}")

    page = search.results_page(search.SearchParams(page_size=10))
    featured_on_page = [item for item in page.items if item.slug.startswith("paid")]

    assert len(featured_on_page) == search.FEATURED_CAP_PER_PAGE
    assert len(page.items) == 10


def test_the_remaining_featured_listings_appear_on_later_pages():
    _cohort(20)
    for i in range(6):
        _featured(f"paid{i}")

    page_two = search.results_page(search.SearchParams(page=2, page_size=10))
    featured_on_page = [item for item in page_two.items if item.slug.startswith("paid")]

    assert len(featured_on_page) == search.FEATURED_CAP_PER_PAGE


def test_pagination_does_not_skip_or_repeat_anyone():
    """The featured offsets are the fiddly part: page two's standard slice has to
    account for how many featured slots page one actually used."""
    _cohort(23)
    for i in range(4):
        _featured(f"paid{i}")

    seen = []
    for number in (1, 2, 3):
        seen += _slugs(search.SearchParams(page=number, page_size=10))

    assert len(seen) == 27
    assert len(set(seen)) == 27


def test_an_expired_featured_window_is_not_featured():
    _cohort(2)
    PractitionerFactory(
        published=True,
        slug="lapsed",
        featured_until=timezone.now() - timezone.timedelta(days=1),
        completeness=0,
    )

    page = search.results_page(search.SearchParams(page_size=10))

    assert page.featured_total == 0


# ---------------------------------------------------------------------------
# What the card is handed
# ---------------------------------------------------------------------------


def test_distance_outside_the_radius_is_not_shown():
    """A practitioner matched because they work online still has a nearest office.
    Printing "14.8 miles away" on a five-mile search reads as a broken filter."""
    practitioner = PractitionerFactory(published=True, slug="online-and-distant")
    _at(practitioner, GUILDFORD)

    page = search.results_page(search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=3))

    assert page.items[0].distance_miles is None


def test_distance_inside_the_radius_is_shown():
    practitioner = PractitionerFactory(published=True, slug="near")
    _at(practitioner, EPSOM)

    page = search.results_page(search.SearchParams(lat=51.3360, lng=-0.2674, radius_miles=10))

    assert page.items[0].distance_miles == pytest.approx(0.0, abs=1.0)


def test_the_card_gets_a_capped_list_of_specialities():
    specialities = [SpecialityFactory(slug=f"s{i}", name=f"Speciality {i}") for i in range(6)]
    PractitionerFactory(published=True, slug="many", specialities=specialities)

    page = search.results_page(search.SearchParams())

    assert len(page.items[0].top_specialities) == search.TOP_SPECIALITIES_ON_CARD


def test_decorating_a_page_does_not_run_a_query_per_result(django_assert_num_queries):
    """Twenty extra round trips on a page with a latency budget is the thing this
    avoids — the specialities come back in one query for the whole page."""
    specialities = [SpecialityFactory(slug=f"s{i}", name=f"S{i}") for i in range(3)]
    for i in range(10):
        PractitionerFactory(published=True, slug=f"p{i}", specialities=specialities)

    # featured count + standard count + featured slice + standard slice, then the
    # three card queries: specialities, languages, public locations. THREE and not
    # three-per-card is the whole point — the number here is allowed to grow when
    # the card learns a new fact, and is not allowed to grow with the page size.
    with django_assert_num_queries(7):
        page = search.results_page(search.SearchParams(page_size=10))
        assert len(page.items) == 10
        assert page.items[0].top_specialities


def test_the_card_queries_do_not_grow_with_the_page(django_assert_num_queries):
    """The guard the count above cannot give on its own: twice the rows, same
    queries. A per-card `practitioner.languages.all` passes the test above at a
    page size of one."""
    specialities = [SpecialityFactory(slug=f"s{i}", name=f"S{i}") for i in range(3)]
    for i in range(20):
        PractitionerFactory(published=True, slug=f"p{i}", specialities=specialities)

    with django_assert_num_queries(7):
        page = search.results_page(search.SearchParams(page_size=20))
        assert len(page.items) == 20


def test_wider_radius_offers_the_next_option_up():
    assert search.wider_radius(10) == 15
    assert search.wider_radius(50) is None


# ---------------------------------------------------------------------------
# Client groups (the gate has its own file; this is the ordinary filtering)
# ---------------------------------------------------------------------------


def test_client_group_filtering_without_minors_needs_no_clearance():
    adults = ClientGroupFactory(slug="adults", name="Adults", is_minors=False)
    PractitionerFactory(published=True, slug="adult-work", client_groups=[adults])

    assert _slugs(search.SearchParams(client_groups=["adults"])) == ["adult-work"]


# ---------------------------------------------------------------------------
# ADOPTION FIX 6 — the text query filters, not just ranks
# ---------------------------------------------------------------------------


def _indexed(**kwargs):
    from apps.directory.services import search_index

    practitioner = PractitionerFactory(published=True, **kwargs)
    search_index.rebuild(practitioner)
    return practitioner


def test_a_query_that_matches_nothing_returns_nothing():
    """As authored, `q` only annotated a rank — so this returned the whole
    directory, presented as though it had matched, and the empty state could never
    appear for a text search."""
    _indexed(slug="alice", full_name="Alice Fernsby")
    _indexed(slug="bob", full_name="Bob Cartwright")

    assert _slugs(search.SearchParams(q="zzzznonsense")) == []


def test_a_query_returns_only_the_practitioners_it_matches():
    wanted = _indexed(slug="wanted", full_name="Marcus Trevelyan")
    _indexed(slug="other", full_name="Grace Fielding")

    assert _slugs(search.SearchParams(q="Trevelyan")) == [wanted.slug]


def test_a_speciality_name_is_searchable_as_text():
    """Band B of the vector. This is what makes the search box useful at all."""
    adhd = SpecialityFactory(slug="adult-adhd", name="Adult ADHD assessment")
    match = _indexed(slug="does-adhd", full_name="Someone", specialities=[adhd])
    _indexed(slug="does-not", full_name="Someone Else")

    assert _slugs(search.SearchParams(q="ADHD")) == [match.slug]


def test_a_blank_query_still_returns_everybody():
    """Browsing is not searching — an empty box must not filter."""
    _indexed(slug="a", full_name="A Person")
    _indexed(slug="b", full_name="B Person")

    assert len(_slugs(search.SearchParams(q="   "))) == 2


def test_the_empty_state_is_reachable_from_the_search_box(client):
    """The phase requires an empty state; one no text query can trigger is half
    built."""
    _indexed(slug="somebody", full_name="Somebody Listed")

    body = client.get("/search/", {"q": "zzzznonsense"}).content.decode()

    assert "No practitioners match these filters" in body


# ---------------------------------------------------------------------------
# The register row's facts (Phase 5c)
# ---------------------------------------------------------------------------


def test_initials_split_on_whitespace_not_on_the_hyphen():
    """Splitting on the hyphen too turns "Marcus Osei-Bonsu" into MB, which is not
    how anybody writes their own initials."""
    assert search._initials("Marcus Osei-Bonsu") == "MO"
    assert search._initials("Priya Sharma") == "PS"
    assert search._initials("Cher") == "C"
    assert search._initials("   ") == ""


def test_fees_are_rendered_from_pence_and_absence_is_a_real_answer():
    """`fee_min` is pence — the unit `fee_max_pence` filters in. A card that
    renders it raw says "£9500 / session"."""

    class Row:
        def __init__(self, low, high):
            self.fee_min, self.fee_max = low, high

    assert search._fee_label(Row(9500, 9500)) == "£95 / session"
    assert search._fee_label(Row(7000, 12000)) == "£70–£120 / session"
    assert search._fee_label(Row(7000, None)) == "£70 / session"
    assert search._fee_label(Row(9550, 9550)) == "£95.50 / session"
    # No fee is no line. "£0" is wrong and "Price on request" is copy nobody wrote.
    assert search._fee_label(Row(None, None)) == ""


def test_one_language_is_not_a_line_on_the_card():
    """Everybody here works in English, so printing it on every row spends a line
    of the register on nothing."""
    language = LanguageFactory(code="en", name="English")
    other = LanguageFactory(code="hi", name="Hindi")

    only_english = PractitionerFactory(published=True, slug="one-tongue", languages=[language])
    bilingual = PractitionerFactory(published=True, slug="two-tongues", languages=[language, other])

    items = [only_english, bilingual]
    search.decorate_cards(items)

    assert items[0].languages_label == ""
    assert "English" in items[1].languages_label and "Hindi" in items[1].languages_label


def test_a_private_address_never_names_its_town_on_a_card():
    """`is_public=False` is usually a home office. Naming its town on a results
    page publishes it by inference — the same rule the browse links follow."""
    practitioner = PractitionerFactory(published=True, slug="works-from-home")
    PractitionerLocationFactory(practitioner=practitioner, city="Hiddenham", is_public=False)

    items = [practitioner]
    search.decorate_cards(items)

    assert items[0].nearest_place == ""


def test_the_status_note_says_different_things_open_and_closed():
    """Open: the fact that makes an impossible appointment possible. Closed: the
    practitioner's own words about when that changes."""
    evenings = PractitionerFactory(published=True, slug="evenings", accepting_new_clients=True)
    evenings.evening_appointments = True
    closed = PractitionerFactory(published=True, slug="closed", accepting_new_clients=False)
    closed.availability_note = "Expected to reopen in October"
    quiet = PractitionerFactory(published=True, slug="quiet", accepting_new_clients=False)

    assert search._status_note(evenings) == "Evening appointments"
    assert search._status_note(closed) == "Expected to reopen in October"
    # No note is no note. "Check back later" is a promise nobody made.
    assert search._status_note(quiet) == ""


# ---------------------------------------------------------------------------
# Facet counts (Phase 5c)
# ---------------------------------------------------------------------------


def test_a_facet_count_is_measured_with_its_own_group_cleared():
    """Count a group against a queryset that already has that group's filter
    applied and every sibling reads 0 the moment you tick one — a sidebar telling
    you there is nothing else to choose is worse than one with no numbers on it."""
    self_pay = FundingOptionFactory(slug="self-pay", name="Self-pay")
    insurance = FundingOptionFactory(slug="insurance", name="Private insurance")

    PractitionerFactory(published=True, slug="a").funding_options.set([self_pay])
    PractitionerFactory(published=True, slug="b").funding_options.set([self_pay])
    PractitionerFactory(published=True, slug="c").funding_options.set([insurance])

    counts = search.facet_counts(search.SearchParams(funding=["self-pay"]))

    assert counts["funding"]["self-pay"] == 2
    # The sibling is still reachable and says so.
    assert counts["funding"]["insurance"] == 1


def test_facet_counts_still_narrow_across_groups():
    """Within a group the selection is cleared; across groups the filters stay on,
    so the number answers "and how many of THESE"."""
    self_pay = FundingOptionFactory(slug="self-pay", name="Self-pay")

    PractitionerFactory(published=True, slug="a", is_prescriber=True).funding_options.set([self_pay])
    PractitionerFactory(published=True, slug="b", is_prescriber=False).funding_options.set([self_pay])

    counts = search.facet_counts(search.SearchParams(is_prescriber=True))

    assert counts["funding"]["self-pay"] == 1


def test_the_under_18_gate_survives_the_count_query():
    """Clearing the speciality selection also clears what `_requests_minor_work`
    reads, so the gate has to be re-applied by hand or a count would be measured
    against a queryset the real search would never return."""
    child_work = SpecialityFactory(slug="child-adhd", name="Child ADHD", implies_minors=True)
    other = SpecialityFactory(slug="adult-adhd", name="Adult ADHD")

    cleared = PractitionerFactory(published=True, slug="cleared", specialities=[child_work, other])
    cleared.minor_work_status = MinorWorkStatus.CLEARED
    cleared.save(update_fields=["minor_work_status"])

    provisional = PractitionerFactory(published=True, slug="prov", specialities=[child_work, other])
    provisional.minor_work_status = MinorWorkStatus.PROVISIONAL
    provisional.save(update_fields=["minor_work_status"])

    counts = search.facet_counts(search.SearchParams(specialities=["child-adhd"]))

    assert counts["speciality"]["adult-adhd"] == 1, "the gate was relaxed by clearing the group"
