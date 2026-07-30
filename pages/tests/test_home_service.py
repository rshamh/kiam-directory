"""``pages.services.home`` — the caching and the rotation.

These are the assertions the brief's gate names ("grid rotates daily and caches")
plus the two safety properties the module's docstring claims for caching primary
keys rather than rows: a stale entry cannot publish a suspended listing, and
nothing user-specific can get in.
"""

from __future__ import annotations

from datetime import date

import pytest
from django.core.cache import cache
from freezegun import freeze_time

from backoffice.services import publication
from directory.factories import PractitionerFactory, PractitionerLocationFactory
from directory.models import Practitioner, PublicationStatus
from directory.services import search as search_service
from pages.services import home

pytestmark = pytest.mark.django_db


@pytest.fixture
def cohort():
    """Twenty published listings, so a reshuffle has somewhere to move things to."""
    return [PractitionerFactory(published=True, slug=f"g{index:02d}", complete=True) for index in range(20)]


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def test_the_grid_selection_changes_from_one_day_to_the_next(cohort):
    """ "Cacheable for 24h, but not the same twelve faces forever."

    Asserted on the SET, not the order: a reordering of the same twelve would leave
    eight practitioners never seen by anybody.
    """
    monday = {p.pk for p in search_service.homepage_grid(12, day=date(2026, 7, 27))}
    tuesday = {p.pk for p in search_service.homepage_grid(12, day=date(2026, 7, 28))}

    assert len(monday) == 12
    assert monday != tuesday


def test_the_same_day_always_gives_the_same_twelve_in_the_same_order(cohort):
    """A reload that reshuffles is disorienting, and this audience has a high rate
    of anxiety and neurodevelopmental conditions (golden rule #3)."""
    first = [p.pk for p in search_service.homepage_grid(12, day=date(2026, 7, 27))]
    second = [p.pk for p in search_service.homepage_grid(12, day=date(2026, 7, 27))]

    assert first == second


def test_the_seed_follows_the_local_date_not_utc(cohort):
    """Phase 5 adoption fix, asserted behaviourally rather than by reading the source.

    `timezone.now().date()` is the UTC date. At 00:30 British Summer Time that is
    *yesterday*, so the grid served yesterday's twelve for an hour while
    `search.services.params.daily_seed()` — the seed for the result shuffle — had
    already rolled over. One clock for both.

    23:30 UTC on 27 July is 00:30 BST on the 28th, which is what makes this the
    exact hour the two disagreed.
    """
    with freeze_time("2026-07-27 23:30:00+00:00"):
        served = [p.pk for p in search_service.homepage_grid(12)]

    assert served == [p.pk for p in search_service.homepage_grid(12, day=date(2026, 7, 28))]
    assert served != [p.pk for p in search_service.homepage_grid(12, day=date(2026, 7, 27))]


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_the_selection_is_cached_under_the_key_publication_already_busts(cohort):
    home.grid()

    payload = cache.get(home.GRID_CACHE_KEY)
    assert payload["version"] == home.CACHE_VERSION
    assert len(payload["ids"]) == 12
    assert home.GRID_CACHE_KEY in publication.CACHE_KEY_PATTERNS


def test_what_is_cached_is_primary_keys_and_not_model_instances(cohort):
    """Pickled rows read back after a migration raise on unpickle and the page 500s
    until somebody flushes Redis — the FACET_CACHE_VERSION lesson. Keys have no
    schema."""
    home.grid()
    payload = cache.get(home.GRID_CACHE_KEY)

    assert all(isinstance(value, str) for value in payload["ids"])
    assert not any(isinstance(value, Practitioner) for value in payload.values())


def test_a_second_render_does_not_re_run_the_selection_query(cohort, monkeypatch):
    home.grid()

    def explode(*args, **kwargs):  # pragma: no cover — a tripwire, not a path
        raise AssertionError("the grid selection was recomputed on a cache hit")

    monkeypatch.setattr(search_service, "homepage_grid", explode)

    assert len(home.grid()) == 12


def test_a_payload_written_by_an_older_deploy_is_rebuilt_not_read(cohort):
    """Versioned inside the value, so a shape change is self-healing rather than a
    manual flush in a runbook."""
    cache.set(home.GRID_CACHE_KEY, {"version": home.CACHE_VERSION - 1, "nonsense": True})

    assert len(home.grid()) == 12
    assert cache.get(home.GRID_CACHE_KEY)["version"] == home.CACHE_VERSION


def test_a_browse_payload_from_an_older_deploy_is_rebuilt_not_read(cohort):
    """The half that was missing, and the omission shipped.

    Phase 5's gate review found this module failing its own rule: `count_label` was
    added to the browse dicts and `FEATURED_CAP_PER_PAGE` to the grid selection, the
    version was not bumped, and the live page served the old payload — browse counts
    with no unit, and four "Paid placement" cards under a promise of three.
    """
    cache.set(home.BROWSE_CACHE_KEY, {"version": home.CACHE_VERSION - 1, "data": {"nonsense": True}})

    data = home.browse_entry_points()

    assert set(data) == {"specialities", "towns"}
    assert cache.get(home.BROWSE_CACHE_KEY)["version"] == home.CACHE_VERSION


def test_every_browse_entry_carries_the_keys_the_template_reads(cohort):
    """A missing key renders as the empty string, silently. `count_label` did."""
    from directory.factories import SpecialityCategoryFactory, SpecialityFactory

    cohort[0].specialities.add(
        SpecialityFactory(slug="adult-adhd", category=SpecialityCategoryFactory(slug="neuro"))
    )
    for practitioner in cohort[:2]:
        PractitionerLocationFactory(practitioner=practitioner, city="Epsom", postcode="KT18 5EP")

    data = home.browse_entry_points()

    assert data["specialities"] and data["towns"]
    for entry in data["specialities"] + data["towns"]:
        assert set(entry) == {"label", "count", "count_label", "url"}, entry
        assert entry["label"] and entry["count"] and entry["count_label"] and entry["url"]


def test_nothing_user_specific_can_reach_the_cached_payload(cohort):
    """None of the three entry points takes a request, which is what makes "do not
    cache anything user-specific" true by construction rather than by care.

    Asserted on the signatures, because a signature is what a refactor changes when
    somebody wants "just the visitor's postcode" in here.
    """
    import inspect

    for name in ("hero", "grid", "browse_entry_points"):
        parameters = inspect.signature(getattr(home, name)).parameters
        assert "request" not in parameters, f"home.{name}() has grown a request argument"


# ---------------------------------------------------------------------------
# The safety property the cache design buys
# ---------------------------------------------------------------------------


def test_suspending_a_listing_removes_it_from_the_grid_within_seconds(cohort, admin_user):
    """`publication.suspend()` busts the key. That is the primary mechanism."""
    home.grid()
    chosen = home.grid()[0]

    publication.suspend(chosen, actor=admin_user, reason="Registration lapsed.")

    assert chosen.pk not in {p.pk for p in home.grid()}


def test_a_stale_cache_still_cannot_show_a_suspended_listing(cohort):
    """The belt-and-braces half, and the reason the cache holds keys rather than
    rows: the hydration query re-applies `status=PUBLISHED`.

    Simulates a `bust_cache()` that never happened — a wrong Redis instance, a
    renamed key, a future refactor. `docs/verification-policy.md`'s worst case is a
    struck-off practitioner live on a Kiam-branded page, and this is the page with
    the most traffic.
    """
    chosen = home.grid()[0]

    # Straight to the column, deliberately: `publication.suspend()` would bust the
    # cache, which is the mechanism this test is standing in for the absence of.
    Practitioner.objects.filter(pk=chosen.pk).update(status=PublicationStatus.SUSPENDED)

    assert cache.get(home.GRID_CACHE_KEY) is not None, "the stale entry must still be there"
    assert chosen.pk not in {p.pk for p in home.grid()}


# ---------------------------------------------------------------------------
# Decoration
# ---------------------------------------------------------------------------


def test_a_grid_card_shows_no_distance_because_nobody_gave_a_location(cohort):
    """A card that printed "0.0 miles away" on the home page would be a lie about
    somebody a visitor might travel to."""
    PractitionerLocationFactory(practitioner=cohort[0])

    assert all(item.distance_miles is None for item in home.grid())


def test_grid_cards_carry_their_speciality_pills_in_one_query(cohort, django_assert_max_num_queries):
    """A card reaching for `practitioner.specialities.all` is a query per card —
    twelve extra round trips on a page with a latency budget."""
    from directory.factories import SpecialityFactory

    speciality = SpecialityFactory(slug="adult-adhd", name="Adult ADHD assessment")
    for practitioner in cohort:
        practitioner.specialities.add(speciality)

    cache.clear()
    with django_assert_max_num_queries(6):
        items = home.grid()
        assert [item.top_specialities for item in items]


# ---------------------------------------------------------------------------
# Browse entry points
# ---------------------------------------------------------------------------


def test_browse_entry_points_are_cached_and_busted_by_publication(cohort, admin_user):
    home.browse_entry_points()

    assert cache.get(home.BROWSE_CACHE_KEY) is not None
    assert home.BROWSE_CACHE_KEY in publication.CACHE_KEY_PATTERNS

    publication.suspend(cohort[0], actor=admin_user, reason="Under investigation.")
    assert cache.get(home.BROWSE_CACHE_KEY) is None


def test_a_town_link_centres_on_an_outward_code_not_the_town_name(cohort):
    """The Phase 5 SEO review's second blocker.

    `?near=Croydon` sent visitors to Croydon, **Cambridgeshire** — `geocode.places()`
    takes the first OS Open Names match with no importance ranking, and three of the
    ten town links resolved to the wrong county. A person typing a town sees the
    resolved label and can correct it; a link on the home page asserts the
    destination.
    """
    for practitioner in cohort[:2]:
        PractitionerLocationFactory(practitioner=practitioner, city="Croydon", postcode="CR0 1LB")

    towns = home.browse_entry_points()["towns"]

    assert towns[0]["label"] == "Croydon"
    assert "near=CR0" in towns[0]["url"]
    assert "near=Croydon" not in towns[0]["url"]


def test_a_town_link_asks_for_a_building_so_its_count_is_not_swamped(cohort):
    """`delivery=in_person` turns off the "…or works online" fallback. The heading is
    "By where they work"; without this, "Epsom — 3 based here" landed on twenty-five
    results and read as a broken filter."""
    for practitioner in cohort[:2]:
        PractitionerLocationFactory(practitioner=practitioner, city="Epsom", postcode="KT18 5EP")

    assert "delivery=in_person" in home.browse_entry_points()["towns"][0]["url"]


def test_a_town_with_no_usable_postcode_is_not_offered(cohort):
    """No outward code means no link. Falling back to `?near=<city>` would put the
    wrong-Croydon bug back on exactly the towns whose data is weakest."""
    for practitioner in cohort[:2]:
        PractitionerLocationFactory(practitioner=practitioner, city="Nowhereton", postcode="")

    assert home.browse_entry_points()["towns"] == []


def test_the_two_browse_lists_label_their_counts_differently(cohort):
    """The numbers count different things, so they must not both say "listed".

    A category count IS a result count. A town count is practitioners with an address
    in that town, while the destination is everyone within ten miles of its outward
    code — legitimately more.
    """
    from directory.factories import SpecialityCategoryFactory, SpecialityFactory

    speciality = SpecialityFactory(slug="adult-adhd", category=SpecialityCategoryFactory(slug="neuro"))
    cohort[0].specialities.add(speciality)
    for practitioner in cohort[:2]:
        PractitionerLocationFactory(practitioner=practitioner, city="Epsom", postcode="KT18 5EP")

    data = home.browse_entry_points()

    assert data["specialities"][0]["count_label"] == "listed"
    assert data["towns"][0]["count_label"] == "based here"


def test_browse_lists_are_capped(cohort):
    """Every one of these is a `noindex, follow` facet URL until Phase 7 replaces
    them with curated landing pages. A home page fanning out into dozens of thin
    permutations is the doorway pattern docs/seo.md forbids."""
    from directory.factories import SpecialityCategoryFactory, SpecialityFactory

    for index in range(home.BROWSE_LIMIT + 5):
        category = SpecialityCategoryFactory(slug=f"cat-{index}", name=f"Category {index}")
        speciality = SpecialityFactory(slug=f"spec-{index}", category=category)
        cohort[0].specialities.add(speciality)
        PractitionerLocationFactory(practitioner=cohort[index], city=f"Town {index}")
        PractitionerLocationFactory(practitioner=cohort[index + 1], city=f"Town {index}")

    data = home.browse_entry_points()

    assert len(data["specialities"]) == home.BROWSE_LIMIT
    assert len(data["towns"]) == home.BROWSE_LIMIT
