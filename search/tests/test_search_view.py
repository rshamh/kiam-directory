"""The /search/ page.

The load-bearing test in here is
`test_search_works_completely_without_javascript`. CLAUDE.md calls the non-JS path
non-negotiable, and it is what keeps the page crawlable and keyboard-usable — so it
is asserted on the full response body, with no HX-Request header, with filters
applied.

Geocoding is patched throughout. These tests are about the view; letting them
reach postcodes.io would make the suite depend on somebody else's uptime and put a
network round trip in every run.
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.gis.geos import Point

from directory.factories import (
    ApproachFactory,
    ClientGroupFactory,
    FundingOptionFactory,
    LanguageFactory,
    PractitionerFactory,
    PractitionerLocationFactory,
    SessionFormatFactory,
    SpecialityCategoryFactory,
    SpecialityFactory,
)
from search.services import geocode

pytestmark = pytest.mark.django_db

URL = "/search/"
EPSOM = (-0.2674, 51.3360)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Nothing in this module may make an HTTP request."""

    def explode(*args, **kwargs):  # pragma: no cover — a tripwire, not a path
        raise AssertionError("a test tried to reach the geocoding API")

    monkeypatch.setattr("search.services.geocode.requests.get", explode)


@pytest.fixture
def epsom(monkeypatch):
    """Make "KT18 5EP" resolve, without asking anybody."""
    monkeypatch.setattr(
        geocode,
        "resolve",
        lambda text: geocode.Place(label="KT18 5EP", lat=51.3360, lng=-0.2674, kind="postcode"),
    )


@pytest.fixture
def vocabulary():
    """One term per facet, so the sidebar renders every group.

    The FilterGroup component skips a facet with no options — an empty fieldset is
    worse than no fieldset — so a sidebar test needs the vocabulary to exist. In
    production `seed_taxonomy` supplies it.
    """
    return {
        "client_group": ClientGroupFactory(slug="adults", name="Adults", is_minors=False),
        "language": LanguageFactory(code="en", name="English"),
        "funding": FundingOptionFactory(slug="self-pay", name="Self-pay"),
        "session_format": SessionFormatFactory(slug="one-to-one", name="One to one"),
    }


@pytest.fixture
def cohort(vocabulary):
    category = SpecialityCategoryFactory(slug="neuro", name="Neurodevelopmental")
    adhd = SpecialityFactory(slug="adult-adhd", name="Adult ADHD assessment", category=category)
    cbt = ApproachFactory(slug="cbt", name="CBT")

    local = PractitionerFactory(
        published=True,
        slug="local-example",
        full_name="Aisha Rahman",
        display_title="Dr",
        post_nominals="MBBS MRCPsych",
        specialities=[adhd],
        approaches=[cbt],
        accepting_new_clients=True,
        verified=True,
        completeness=90,
    )
    PractitionerLocationFactory(practitioner=local, geo=Point(*EPSOM, srid=4326), city="Epsom")

    remote = PractitionerFactory(
        published=True,
        slug="remote-example",
        full_name="Jordan Bell",
        display_title="Dr",
        specialities=[adhd],
        online_only=True,
        accepting_new_clients=False,
    )
    return local, remote


# ---------------------------------------------------------------------------
# The non-negotiable one
# ---------------------------------------------------------------------------


def test_search_works_completely_without_javascript(client, cohort, epsom):
    """No HX-Request header, filters applied, full results in the body.

    This is what keeps /search/ crawlable and keyboard-usable (CLAUDE.md, "HTMX
    conventions"). If this fails, the page is a JavaScript application wearing a
    URL.
    """
    local, remote = cohort

    response = client.get(
        URL,
        {
            "q": "ADHD",
            "near": "KT18 5EP",
            "radius": "25",
            "speciality": "adult-adhd",
            "approach": "cbt",
            "accepting": "1",
            "verified": "1",
        },
    )
    body = response.content.decode()

    assert response.status_code == 200

    # A whole document, not a fragment.
    assert "<!doctype html>" in body.lower()
    assert "</html>" in body

    # The results are IN the body — not fetched later.
    assert local.full_name in body
    assert f'href="/p/{local.slug}/"' in body

    # And the filter that was applied is reflected back, so the form round-trips.
    assert 'name="q"' in body
    assert 'value="ADHD"' in body
    assert 'value="KT18 5EP"' in body


def test_the_sidebar_is_a_plain_form_with_a_visible_submit(client, cohort):
    """Degrades to a plain form with a visible submit button — not optional."""
    body = client.get(URL).content.decode()

    assert '<form method="get"' in body
    assert 'action="/search/"' in body
    assert body.count('type="submit"') >= 1
    # Real fieldsets with real legends, so the groups are navigable.
    assert body.count("<fieldset") >= 5
    assert body.count("<legend") >= 5


def test_every_filter_is_a_real_form_control(client, cohort):
    """Each facet in the brief has to be reachable without script."""
    body = client.get(URL).content.decode()

    for name in (
        "q",
        "near",
        "radius",
        "speciality",
        "category",
        "profession",
        "approach",
        "group",
        "language",
        "delivery",
        "gender",
        "accepting",
        "wait",
        "evening",
        "weekend",
        "fee_max",
        "funding",
        "prescriber",
        "verified",
        "step_free",
        "parking",
        "hearing_loop",
        "experience",
        "format",
    ):
        assert f'name="{name}"' in body, f"no form control posts `{name}`"


def test_the_radius_control_is_a_select_not_a_slider(client, cohort):
    """A slider needs a text alternative and is hard with a motor impairment.
    Seven discrete values is a select."""
    body = client.get(URL).content.decode()

    assert 'id="search-radius"' in body
    assert '<select class="ds-control ds-control--select" id="search-radius"' in body
    assert 'type="range"' not in body


def test_pagination_links_are_real_anchors(client):
    for i in range(25):
        PractitionerFactory(published=True, slug=f"p{i:02d}")

    body = client.get(URL).content.decode()

    assert "dir-pagination" in body
    assert re.search(r'<a class="dir-pagination__link"\s+id="pagination-next"\s+href="\?[^"]*page=2', body)


def test_pagination_links_carry_an_id_so_focus_survives_the_swap():
    """htmx 2 restores focus after a swap only when the focused element had an `id`
    (`if (s.elt && !le(s.elt) && ee(s.elt, "id"))`). These anchors are inside
    #results and are destroyed by it, so without one, activating "Next" dropped
    focus to <body> — with the whole filter sidebar between the user and the link
    they had just used."""
    from django.test import Client

    for i in range(25):
        PractitionerFactory(published=True, slug=f"p{i:02d}")

    body = Client().get(URL).content.decode()

    assert 'id="pagination-next"' in body


# ---------------------------------------------------------------------------
# HTMX
# ---------------------------------------------------------------------------


def test_an_htmx_request_returns_only_the_results_fragment(client, cohort):
    response = client.get(URL, HTTP_HX_REQUEST="true")
    body = response.content.decode()

    assert response.status_code == 200
    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()
    assert "dir-search__sidebar" not in body
    # But the results themselves are there.
    assert cohort[0].full_name in body


def test_the_fragment_updates_the_persistent_live_region(client, cohort):
    """The count element lives OUTSIDE #results and has its inner HTML swapped.

    A live region that is itself replaced by the swap is not reliably announced;
    one that persists and has its contents changed is. Same lesson as the Phase 3
    contact reveal.
    """
    body = client.get(URL, HTTP_HX_REQUEST="true").content.decode()

    assert 'hx-swap-oob="innerHTML:#result-count"' in body


def test_the_full_page_does_not_emit_the_out_of_band_element(client, cohort):
    """It would render as stray text — the region is already on the page."""
    body = client.get(URL).content.decode()

    assert "hx-swap-oob" not in body
    assert 'id="result-count"' in body


def test_the_fragment_and_the_full_page_show_the_same_practitioners(client, cohort):
    """One template for both, so the enhanced path cannot drift from the plain one."""
    full = client.get(URL, {"speciality": "adult-adhd"}).content.decode()
    fragment = client.get(URL, {"speciality": "adult-adhd"}, HTTP_HX_REQUEST="true").content.decode()

    for practitioner in cohort:
        assert (practitioner.full_name in full) == (practitioner.full_name in fragment)


# ---------------------------------------------------------------------------
# SEO
# ---------------------------------------------------------------------------


def test_the_bare_search_page_is_indexable(client, cohort):
    body = client.get(URL).content.decode()
    assert 'name="robots"' not in body


@pytest.mark.parametrize(
    "query",
    [
        {"speciality": "adult-adhd"},
        {"q": "adhd"},
        {"verified": "1"},
        {"page": "2"},
        {"radius": "25"},
    ],
)
def test_every_faceted_url_is_noindex_follow(client, cohort, query):
    """Without this the facet engine generates millions of thin permutations and
    the subdomain reads as a doorway farm (docs/seo.md)."""
    body = client.get(URL, query).content.decode()

    assert '<meta name="robots" content="noindex, follow">' in body


def test_a_faceted_url_canonicalises_to_the_bare_search_page(client, cohort):
    body = client.get(URL, {"speciality": "adult-adhd", "verified": "1"}).content.decode()

    assert '<link rel="canonical" href="http://testserver/search/"' in body
    assert "speciality" not in body.split("<title>")[0]


def test_the_bare_search_page_is_in_the_sitemap_and_no_facet_is(client, cohort):
    """The bare page is the canonical target of every facet permutation.

    Leaving it out — as the first version did — made the canonical point at a URL
    in no sitemap, linked from nowhere on the site, and denied by the home page.
    A canonical to a URL nothing reaches is a dead end, not a signal.
    """
    body = client.get("/sitemap.xml").content.decode()

    assert "<loc>https://testserver/search/</loc>" in body
    assert "/search/?" not in body
    assert "/search/places/" not in body


def test_the_search_page_is_reachable_from_the_navigation(client, cohort):
    """A page nothing links to is not published, whatever the URLconf says."""
    body = client.get("/").content.decode()

    assert 'href="/search/"' in body


def test_the_home_page_does_not_deny_that_search_exists(client):
    """It did, while emitting a SearchAction pointing at it — the same
    machine-readable/human-readable contradiction Phase 3 hit with the listings."""
    body = client.get("/").content.decode()

    assert "Search and the browse pages are not available" not in body
    assert "SearchAction" in body


def test_the_place_suggestions_partial_is_not_indexable(client):
    """Head-less HTML cannot carry a noindex meta tag, and `?near=` is unbounded —
    one thin URL per typed prefix. Header plus robots.txt instead."""
    response = client.get("/search/places/", {"near": "Eps"})

    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert "Disallow: /search/places/" in client.get("/robots.txt").content.decode()


def test_the_search_page_emits_a_search_results_page_graph(client, cohort):
    body = client.get(URL).content.decode()
    raw = body.split('<script type="application/ld+json">')[1].split("</script>")[0]

    document = json.loads(raw)
    types = [node["@type"] for node in document["@graph"]]
    assert types == ["SearchResultsPage"]
    # Emphatically no ItemList of the practitioners: the page is noindex, and each
    # of them already has a Person node on their own profile.
    assert "ItemList" not in body
    assert "AggregateRating" not in body


def test_the_home_page_advertises_the_search_action(client):
    body = client.get("/").content.decode()

    assert "SearchAction" in body
    assert "search_term_string" in body


def test_the_search_page_carries_the_independence_notice(client, cohort):
    """docs/content-compliance.md §5 — every results page, verbatim."""
    body = " ".join(client.get(URL).content.decode().split())

    assert (
        "Practitioners listed in the Kiam Clinic Directory are independent professionals. "
        "They are not employed by, or part of the clinical team at, Kiam Clinic. Clients "
        "arrange appointments and payment directly with the practitioner." in body
    )


def test_the_search_page_carries_the_crisis_signposting(client, cohort):
    """Asserted against the constant, not a substring, so a reword cannot pass
    silently — crisis wording is Dr. Abbass's gate."""
    from pages import nav

    body = " ".join(client.get(URL).content.decode().split())

    assert nav.CRISIS_SIGNPOSTING in body


def test_the_search_page_has_exactly_one_h1(client, cohort):
    assert client.get(URL).content.decode().count("<h1") == 1


def test_search_heading_order_never_skips_a_level(client, cohort):
    main = client.get(URL).content.decode().split("<main", 1)[1].split("</main>", 1)[0]
    levels = [int(m) for m in re.findall(r"<h([1-6])[ >]", main)]

    assert levels[0] == 1
    for previous, current in zip(levels, levels[1:], strict=False):
        assert current <= previous + 1, f"h{previous} is followed by h{current}"


# ---------------------------------------------------------------------------
# Result cards
# ---------------------------------------------------------------------------


def test_a_result_card_shows_what_the_brief_asks_for(client, cohort, epsom):
    local, _ = cohort

    body = client.get(URL, {"near": "KT18 5EP", "radius": "25"}).content.decode()

    assert "Dr Aisha Rahman" in body
    assert "MBBS MRCPsych" in body
    assert "Credentials checked" in body
    assert "Adult ADHD assessment" in body
    assert "Accepting new clients" in body
    assert f'href="/p/{local.slug}/"' in body
    assert "miles away" in body


def test_an_online_practitioner_is_labelled_online_not_zero_miles(client, cohort, epsom):
    body = client.get(URL, {"near": "KT18 5EP", "radius": "25"}).content.decode()

    assert "Online" in body


def test_a_card_never_leaks_contact_details(client, cohort):
    """Same rule as the profile: the reveal is on the profile page, behind a POST."""
    local, _ = cohort
    from directory.models import Practitioner

    Practitioner.objects.filter(pk=local.pk).update(
        public_email="aisha@example.com", public_phone="07700 900123"
    )

    main = client.get(URL).content.decode().split("<main", 1)[1].split("</main>", 1)[0]

    assert "aisha@example.com" not in main
    assert "07700 900123" not in main
    assert "mailto:" not in main


def test_the_whole_card_is_not_one_giant_link(client, cohort):
    """A card-sized anchor gives a screen reader one link whose name is the entire
    card read aloud. The name is the link."""
    body = client.get(URL).content.decode()

    assert '<article class="ds-card dir-practitioner' in body
    assert '<a href="/p/local-example/"' in body


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------


def test_the_empty_state_offers_a_wider_radius(client, epsom):
    PractitionerFactory(published=True, slug="far-away", in_person_only=True)

    body = client.get(URL, {"near": "KT18 5EP", "radius": "1", "verified": "1"}).content.decode()

    assert "No practitioners match these filters" in body
    assert "Search within 3 miles" in body


def test_the_empty_state_offers_to_clear_the_filters(client, cohort):
    body = client.get(URL, {"speciality": "nothing-matches-this"}).content.decode()

    assert "No practitioners match these filters" in body
    assert "Start again with no filters" in body


def test_the_empty_state_suggests_a_location_when_none_was_given(client):
    body = client.get(URL).content.decode()

    assert "Add a postcode or town" in body


def test_a_loading_indicator_exists_and_is_hidden_until_needed(client, cohort):
    body = client.get(URL).content.decode()

    assert 'id="search-busy"' in body
    assert "htmx-indicator" in body


def test_an_unresolvable_location_still_returns_results(client, cohort, monkeypatch):
    """A geocoding failure is not a search failure. The alternative is answering
    "we could not reach our mapping provider" to somebody looking for a therapist."""
    monkeypatch.setattr(geocode, "resolve", lambda text: None)

    response = client.get(URL, {"near": "not a place at all"})
    body = response.content.decode()

    assert response.status_code == 200
    assert cohort[0].full_name in body
    assert "didn&#x27;t recognise" in body or "didn't recognise" in body


def test_a_geocoder_outage_does_not_break_the_page(client, cohort, monkeypatch):
    """Not just a miss — an exception escaping the service.

    `geocode.resolve` is built not to raise, so this is belt and braces. It is worth
    having because the promise is "a geocoder failure is never a search failure",
    and a promise kept in exactly one place is one refactor away from being kept
    nowhere.
    """

    def boom(text):
        raise TimeoutError("postcodes.io is down")

    monkeypatch.setattr(geocode, "resolve", boom)

    response = client.get(URL, {"near": "KT18 5EP"})
    body = response.content.decode()

    assert response.status_code == 200
    assert cohort[0].full_name in body
    assert "didn&#x27;t recognise" in body or "didn't recognise" in body


# ---------------------------------------------------------------------------
# Filter chips
# ---------------------------------------------------------------------------


def test_applied_filters_are_listed_with_human_labels(client, cohort):
    body = client.get(URL, {"speciality": "adult-adhd", "verified": "1"}).content.decode()

    assert "Adult ADHD assessment" in body
    assert "Verified only" in body
    assert "remove this filter" in body


def test_removing_a_chip_drops_only_that_filter(client, cohort):
    body = client.get(URL, {"speciality": "adult-adhd", "verified": "1"}).content.decode()

    chips = re.findall(r'class="dir-chips__remove"\s+href="([^"]*)"', body)
    assert chips
    # The link that removes `verified` must still carry the speciality.
    assert any("speciality=adult-adhd" in href and "verified" not in href for href in chips)


# ---------------------------------------------------------------------------
# Place suggestions
# ---------------------------------------------------------------------------


def test_place_suggestions_return_datalist_options(client, monkeypatch):
    monkeypatch.setattr(
        geocode,
        "places",
        lambda query, limit=8: [geocode.Place(label="Epsom, Surrey", lat=51.3, lng=-0.26)],
    )

    body = client.get("/search/places/", {"near": "Eps"}).content.decode()

    assert '<option value="Epsom, Surrey"></option>' in body


def test_place_suggestions_are_empty_without_a_query(client):
    body = client.get("/search/places/").content.decode()

    assert "<option" not in body


def test_the_location_field_uses_a_native_datalist(client, cohort):
    """Rather than a hand-rolled combobox: the browser supplies the whole keyboard
    pattern, and a suggestion request that never lands leaves a working text box."""
    body = client.get(URL).content.decode()

    assert 'list="place-options"' in body
    assert '<datalist id="place-options">' in body


# ---------------------------------------------------------------------------
# Fixes from the Phase 4 gate reviews
# ---------------------------------------------------------------------------


def test_the_page_title_never_echoes_the_query(client, cohort):
    """A search for a prescription-only medicine used to publish it in <title> AND
    og:title — the field chat clients read to build a link preview card.

    docs/content-compliance.md §1 names meta titles explicitly, and this is Kiam's
    own markup, so the submission lint never sees it. The mechanical echo into the
    input's `value` stays, because that is how a search box works.
    """
    body = client.get(URL, {"q": "methylphenidate 27mg"}).content.decode()

    head = body.split("</head>", 1)[0]
    assert "methylphenidate" not in head
    # But the box still shows what they typed.
    assert 'value="methylphenidate 27mg"' in body


def test_there_is_a_bypass_past_the_filters(client, cohort):
    """WCAG 2.4.1. Seeded with the real taxonomy the sidebar is ~196 tab stops, and
    the chrome's skip link lands above it."""
    body = client.get(URL).content.decode()

    assert 'href="#results"' in body
    assert "Skip to results" in body
    assert body.index("Skip to results") < body.index("dir-search__sidebar")


def test_the_big_speciality_group_is_collapsed_by_default(client, cohort):
    """138 specialities in one open <details> is what made the sidebar 8,000px tall."""
    body = client.get(URL).content.decode()
    block = body.split("<summary>\n      What they treat", 1)[0].rsplit("<details", 1)[1]

    assert "open" not in block


def test_a_group_holding_a_selection_is_open(client, cohort):
    """A closed <details> removes its contents from the accessibility tree, so a
    shared URL would hide an applied filter rather than merely scroll past it."""
    body = client.get(URL, {"speciality": "adult-adhd"}).content.decode()

    assert re.search(r'<details class="dir-filter-block"\s+open>', body)
    assert "selected</span>" in body


def test_removing_a_filter_is_a_plain_navigation(client, cohort):
    """With `hx-get` the chip swapped only #results, leaving its own checkbox
    checked — so the next change event re-applied the filter the visitor had just
    removed (WCAG 4.1.2)."""
    body = client.get(URL, {"verified": "1"}).content.decode()
    chips = re.search(r'<a class="dir-chips__remove"[^>]*>', body).group(0)

    assert "hx-get" not in chips
    assert "href=" in chips


def test_an_unresolved_location_is_announced_in_the_live_region(client, cohort, monkeypatch):
    """The visible notice is created BY the swap, so it is never announced — the
    same trap the count region avoids one element away. Previously the only signal
    was an absence: the count simply dropped its "within N miles" clause."""
    monkeypatch.setattr(geocode, "resolve", lambda text: None)

    body = client.get(URL, {"near": "nowhere at all"}, HTTP_HX_REQUEST="true").content.decode()
    announced = body.split('hx-swap-oob="innerHTML:#result-count">', 1)[1].split("</span>", 1)[0]

    assert "didn&#x27;t recognise" in announced or "didn't recognise" in announced


def test_filter_changes_replace_history_rather_than_stacking_it(client, cohort):
    """Ticking eight filters used to mean eight Back presses to leave the page."""
    body = client.get(URL).content.decode()

    assert 'hx-replace-url="true"' in body
    assert 'hx-push-url="true"' not in body


def test_the_card_uses_the_shared_verified_badge(client, cohort):
    """Not a second copy. The badge's own docstring requires the "what this does and
    does not mean" link to be one click from wherever the claim appears — on a
    results page the claim appears once per card."""
    body = client.get(URL).content.decode()

    assert "Credentials checked" in body
    assert "/how-verification-works/" in body
    assert "What this does and does not mean" in body


def test_the_search_landmark_does_not_wrap_the_results(client, cohort):
    """A landmark named "Search practitioners" containing twenty result cards helps
    nobody navigating by landmark."""
    body = client.get(URL).content.decode()

    assert 'class="dir-searchbar" role="search"' in body
    assert (
        '<form method="get"\n        action="/search/"\n        class="dir-search__form"\n        role="search"'
        not in body
    )


def test_lists_keep_their_semantics_when_markers_are_removed(client, cohort):
    """WebKit drops the implicit list role from a list with `list-style: none`, and
    "list, 20 items" is how a VoiceOver user learns how many results there are."""
    body = client.get(URL).content.decode()

    assert '<ol class="dir-results" role="list">' in body


def test_only_the_first_headshot_is_eager(client, cohort):
    """`loading="lazy"` on the likely LCP element is a Core Web Vitals
    anti-pattern; docs/seo.md asks for lazy *below* the fold."""
    from django.core.files.base import ContentFile

    for practitioner in cohort:
        practitioner.headshot.save(f"{practitioner.slug}.jpg", ContentFile(b"not-a-real-jpeg"), save=True)

    body = client.get(URL).content.decode()

    assert body.count('loading="eager"') == 1
    assert 'fetchpriority="high"' in body
    assert 'width="80"' in body and 'height="80"' in body


# ---------------------------------------------------------------------------
# Paid placement, and the sidebar's real size
# ---------------------------------------------------------------------------


def test_a_featured_listing_is_labelled_at_the_point_of_display(client, vocabulary):
    """Nothing is featured yet, and there was no test — so a template refactor
    could have dropped the label and the suite would have stayed green. The first
    person to notice would have been a paying customer's competitor
    (docs/content-compliance.md §6)."""
    from django.utils import timezone

    PractitionerFactory(
        published=True,
        slug="paid-example",
        full_name="Featured Practitioner",
        featured_until=timezone.now() + timezone.timedelta(days=30),
    )

    body = client.get(URL).content.decode()
    results = body.split('<div id="results">', 1)[1]

    assert "Paid placement" in results
    assert "dir-practitioner--featured" in results


def test_an_unfeatured_listing_carries_no_label(client, cohort):
    """Scoped to the results: "Paid placement" also appears in the ranking
    disclosure, which explains what the label means."""
    body = client.get(URL).content.decode()
    results = body.split('<div id="results">', 1)[1]

    assert "Paid placement" not in results
    assert "dir-practitioner--featured" not in results


def test_the_page_states_how_results_are_ordered(client, cohort):
    """A per-item label satisfies CAP; a ranked list where payment affects position
    also needs the main ranking parameters stated (DMCCA 2024)."""
    body = client.get(URL).content.decode()

    assert "How these results are ordered" in body
    assert "Paid placement" in body or "featured" in body
    assert "does not rank practitioners by quality" in body


def test_the_sidebar_stays_navigable_with_the_real_taxonomy(client, django_assert_max_num_queries):
    """The test that would have caught the 196-tab-stop sidebar when it was written.

    The other sidebar tests use a one-term-per-facet fixture, so none of them had
    ever rendered the real vocabulary — 18 categories and 138 specialities, which is
    what made the sidebar ~8,000px tall with the results below all of it.

    Asserts a ceiling on the controls a keyboard user meets before #results, and
    that there is a bypass regardless.
    """
    from django.core.management import call_command

    call_command("seed_taxonomy", verbosity=0)
    PractitionerFactory(published=True, slug="somebody")

    body = client.get(URL).content.decode()
    before_results = body.split('<div id="results">', 1)[0]

    # Everything inside a collapsed <details> is out of the tab order, so the count
    # that matters is what is left open.
    open_blocks = re.findall(r'<details class="dir-filter-block"\s+open>.*?</details>', before_results, re.S)
    focusable_in_open_groups = sum(block.count("<input") for block in open_blocks)

    assert focusable_in_open_groups < 60, (
        f"{focusable_in_open_groups} filter controls are open by default — the sidebar is a wall again"
    )
    assert "Skip to results" in before_results


def test_no_template_comment_leaks_into_the_page(client, cohort):
    """Django's `{# … #}` is a SINGLE-LINE comment.

    A multi-line one is not a comment at all — it renders as page text, and four of
    them did, one landing where the radius label should have been. Only looking at
    the rendered page showed it; the suite was green throughout.
    """
    for url in (URL, "/", "/p/local-example/"):
        body = client.get(url).content.decode()
        assert "{#" not in body, f"an unclosed template comment reached {url}"
        assert "#}" not in body, f"an unclosed template comment reached {url}"
        # The specific escapee, and the shape of the mistake.
        assert "announces as" not in body
        assert "{% comment %}" not in body


def test_uploaded_media_is_routed_in_development_only():
    """`runserver` serves STATIC_URL and nothing else, so without a MEDIA_URL route
    every headshot 404s locally — on search results AND on the profile page. That
    went unnoticed from Phase 3 until a development database had headshots in it to
    look at.

    A STRUCTURAL assertion, deliberately. The root URLconf is built once at import
    time under the settings then in force, and the test settings have DEBUG off, so
    flipping `settings.DEBUG` here cannot make the route appear — and reloading the
    URLconf mid-suite leaks into every test that follows. What can be checked is
    that the route exists and that it is inside the `if settings.DEBUG` guard, which
    is the part that would be wrong to lose in either direction: no route breaks
    development, an unguarded one puts `django.views.static.serve` in production.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "config" / "urls.py").read_text()

    guard = source.index("if settings.DEBUG:")
    media = source.index("static(settings.MEDIA_URL")

    assert media > guard, "the media route must be inside the DEBUG guard"
    assert "document_root=settings.MEDIA_ROOT" in source
