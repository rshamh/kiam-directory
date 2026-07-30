"""The home page.

The load-bearing test in here is
``test_the_hero_search_works_completely_without_javascript``. The brief calls the
hero "the most important interaction on the site"; CLAUDE.md's HTMX rule makes the
non-JS path non-negotiable everywhere. So it is asserted on the full response body,
with no ``HX-Request`` header, against the markup a browser with script disabled
would get.

Most of the rest is golden rule #1 and the independence rules applied to the one
page with the most authority on the subdomain.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from directory.factories import (
    PractitionerFactory,
    PractitionerLocationFactory,
    SpecialityCategoryFactory,
    SpecialityFactory,
)
from pages import nav

pytestmark = pytest.mark.django_db

URL = "/"


def grid_markup(body: str) -> str:
    """Just the grid's own markup.

    Not `split("</ul>")`: every card contains its own `<ul>` of speciality pills, so
    the first closing tag is inside card one. The disclosure paragraph that follows
    the list is the reliable boundary.
    """
    return body.split('class="dir-home-grid"', 1)[1].split("dir-home-grid__note", 1)[0]


def give_everyone_a_headshot(listings):
    """A headshot on EVERY listing.

    Not just `listings[0]`: the grid shows twelve of fifteen and which twelve is
    decided by an MD5 shuffle over primary keys, which are fresh UUIDs on every run.
    Photographing one practitioner and asserting their picture appears is a test
    that passes four times out of five.
    """
    import io

    from django.core.files.base import ContentFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1600), color=(120, 160, 150)).save(buffer, format="JPEG")
    data = buffer.getvalue()

    for practitioner in listings:
        practitioner.headshot.save(f"{practitioner.slug}.jpg", ContentFile(data), save=True)


def flat(body: str) -> str:
    """The body with runs of whitespace collapsed, for asserting on prose.

    Template copy is wrapped to 90 columns, so a sentence that reads as one line in
    the file arrives with newlines and indentation in the middle of it.
    """
    return " ".join(body.split())


@pytest.fixture
def listings():
    """Fifteen published listings — more than the grid shows, so the cap is real."""
    category = SpecialityCategoryFactory(slug="neuro", name="Neurodevelopmental")
    adhd = SpecialityFactory(slug="adult-adhd", name="Adult ADHD assessment", category=category)

    made = []
    for index in range(15):
        practitioner = PractitionerFactory(
            published=True,
            slug=f"listed-{index:02d}",
            full_name=f"Practitioner {index:02d}",
            display_title="Dr",
            specialities=[adhd],
            complete=True,
        )
        made.append(practitioner)
    return made


# ---------------------------------------------------------------------------
# The non-negotiable one
# ---------------------------------------------------------------------------


def test_the_hero_search_works_completely_without_javascript(client, listings):
    """A plain form, a plain GET, a real submit button, pointed at /search/.

    No `hx-get` on the form itself: the only enhancement in the hero is the
    location field's datalist request, and its absence leaves a working text box.
    """
    body = client.get(URL).content.decode()

    assert '<form method="get" action="/search/" class="dir-hero__form">' in body
    assert 'name="q"' in body
    assert 'name="near"' in body
    assert 'name="radius"' in body
    assert 'type="submit"' in body

    # The form is not an HTMX application wearing a URL.
    form = body.split('class="dir-hero__form"', 1)[1].split("</form>", 1)[0]
    assert "hx-post" not in form
    assert 'hx-target="#results"' not in form


def test_the_hero_submits_a_real_search_that_returns_results(client, listings):
    """End to end, the way somebody with script off actually uses it: read the
    action and the field names off the home page, then GET that URL."""
    response = client.get("/search/", {"q": "ADHD", "radius": "10"})
    body = response.content.decode()

    assert response.status_code == 200
    assert "Practitioner 00" in body or "Practitioner 01" in body


def test_the_three_hero_fields_are_labelled_and_the_radius_is_a_select(client):
    """Keyboard-alone operation starts with every control having a real label.
    A slider needs a text alternative and is hard with a motor impairment."""
    body = client.get(URL).content.decode()

    assert '<label for="search-q">' in body
    assert '<label for="search-near">' in body
    assert '<label for="search-radius">' in body
    assert '<select class="ds-control ds-control--select" id="search-radius"' in body
    assert 'type="range"' not in body


def test_the_location_field_keeps_its_autocomplete(client):
    body = client.get(URL).content.decode()

    assert 'list="place-options"' in body
    assert '<datalist id="place-options">' in body
    assert reverse("search:place_suggestions") in body


def test_the_hero_search_is_the_shared_component_not_a_copy(client):
    """One implementation of the keyboard pattern. If the hero grows its own
    markup, this fails and the a11y work has to be done twice."""
    body = client.get(URL).content.decode()

    assert 'class="dir-searchbar" role="search"' in body


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_the_grid_shows_twelve_of_fifteen(client, listings):
    grid = grid_markup(client.get(URL).content.decode())

    assert grid.count("dir-practitioner__name") == 12


def test_the_grid_excludes_listings_that_are_not_complete_enough(client):
    """`complete=True` fills in the intro, photo, contact route, taxonomy and
    credentials that make the score 100 — the factory makes the data, never the
    number, since Phase 6 gave `completeness` one writer and a signal."""
    from directory.services import completeness

    thorough = PractitionerFactory(
        published=True, slug="thorough", full_name="Thorough Listing", complete=True
    )
    sparse = PractitionerFactory(published=True, slug="sparse", full_name="Sparse Listing")

    thorough.refresh_from_db()
    sparse.refresh_from_db()
    assert thorough.completeness >= completeness.report(thorough).score >= 70
    assert sparse.completeness < 70

    body = client.get(URL).content.decode()

    assert "Thorough Listing" in body
    assert "Sparse Listing" not in body


def test_the_grid_never_shows_a_listing_that_is_not_published(client):
    PractitionerFactory(slug="draft-one", full_name="Draft Listing", complete=True)

    assert "Draft Listing" not in client.get(URL).content.decode()


def test_the_grid_uses_the_shared_card_so_the_badge_comes_with_it(client, listings):
    """Not a second card. The badge's own docstring requires the "what this does
    and does not mean" link to be one click from wherever the claim appears."""
    listings[0].refresh_from_db()
    body = client.get(URL).content.decode()

    assert '<article class="ds-card dir-practitioner' in body
    assert "/how-verification-works/" in body


def test_a_card_on_the_home_page_never_leaks_contact_details(client, listings):
    """Same rule as the profile and the results page: the reveal is a POST on the
    practitioner's own page."""
    from directory.models import Practitioner

    Practitioner.objects.filter(pk=listings[0].pk).update(
        public_email="somebody@example.com", public_phone="07700 900123"
    )

    main = client.get(URL).content.decode().split("<main", 1)[1].split("</main>", 1)[0]

    assert "somebody@example.com" not in main
    assert "07700 900123" not in main
    assert "mailto:" not in main


def test_every_grid_headshot_is_lazy_and_none_claims_high_priority(client, listings):
    """The grid sits below the hero, so all of it is below the fold. An eager
    `fetchpriority="high"` image down here competes with the real LCP element —
    which Lighthouse confirms is the hero lede, a text node."""
    give_everyone_a_headshot(listings)

    body = client.get(URL).content.decode()

    assert 'loading="eager"' not in body
    assert "fetchpriority" not in body
    assert 'loading="lazy"' in body


def test_grid_headshots_declare_their_box_so_nothing_reflows(client, listings):
    """Lighthouse `unsized-images` passes and CLS measured 0.001 because of these
    two attributes. They are the CSS box, not the file's own dimensions — reading
    those means opening the file through the storage backend on every render."""
    give_everyone_a_headshot(listings)

    body = client.get(URL).content.decode()

    assert 'width="80"' in body
    assert 'height="80"' in body


def test_grid_headshots_are_served_responsive(client, listings):
    """docs/seo.md §Performance. A phone photo is 1500–3000px; the box is 80. Twelve
    originals to paint twelve postage stamps is the largest download on the page."""
    from directory.services import images

    give_everyone_a_headshot(listings)

    body = client.get(URL).content.decode()

    assert 'srcset="' in body
    assert f'sizes="{images.HEADSHOT_SIZES}"' in body
    for width in images.HEADSHOT_WIDTHS:
        assert f"-{width}.jpg {width}w" in body


def test_a_listing_with_no_renditions_still_renders_its_headshot(client, listings):
    """`srcset` is all-or-nothing: a candidate URL that 404s is a broken image. So an
    unreadable original falls back to the plain `src` rather than to nothing."""
    from django.core.files.base import ContentFile

    for index, practitioner in enumerate(listings):
        practitioner.headshot.save(f"broken-{index}.jpg", ContentFile(b"not-a-real-jpeg"), save=True)

    body = client.get(URL).content.decode()

    assert "broken-" in body
    assert "srcset" not in body


def test_the_empty_grid_says_so_rather_than_rendering_nothing(client):
    """Reachable with a real database: the grid needs completeness >= 70."""
    PractitionerFactory(published=True, slug="sparse")  # nothing filled in: scores 10

    body = client.get(URL).content.decode()

    assert "No listings to show here yet" in body
    assert 'href="/search/"' in body


# ---------------------------------------------------------------------------
# Paid placement
# ---------------------------------------------------------------------------


def test_a_featured_listing_takes_the_first_slot_and_is_labelled(client, listings):
    """Nothing is featured yet. The label and the cap exist before the first person
    pays, because undisclosed paid ranking breaches CAP rules
    (docs/content-compliance.md §6)."""
    from django.utils import timezone

    from directory.models import Practitioner

    Practitioner.objects.filter(pk=listings[3].pk).update(
        featured_until=timezone.now() + timezone.timedelta(days=30), full_name="Paid Listing"
    )

    grid = grid_markup(client.get(URL).content.decode())

    assert "Paid placement" in grid
    assert "dir-practitioner--featured" in grid
    # First card in the list, whatever the day's shuffle did to the other eleven.
    first_card = grid.split("dir-home-grid__item", 2)[1]
    assert "Paid Listing" in first_card


def test_an_unfeatured_grid_carries_no_paid_label_on_a_card(client, listings):
    """Scoped to the cards: the disclosure paragraph below the grid explains what
    the label means and mentions it by name."""
    grid = grid_markup(client.get(URL).content.decode())

    assert "Paid placement" not in grid
    assert "dir-practitioner--featured" not in grid


def test_the_featured_cap_applies_to_the_grid(client, listings):
    """The Phase 5 compliance review's warning, and it made the disclosure false.

    `homepage_grid()` ordered `-featured, shuffle` and sliced, so FOUR paid listings
    took the first four of twelve slots while the paragraph below promised no more
    than three. In principle all twelve could be paid. This is what ADOPTION
    CHANGE (4) fixed for `/search/`; the grid needed it too
    (docs/content-compliance.md §6).
    """
    from django.utils import timezone

    from directory.models import Practitioner
    from directory.services.search import FEATURED_CAP_PER_PAGE

    for index in range(6):
        Practitioner.objects.filter(pk=listings[index].pk).update(
            featured_until=timezone.now() + timezone.timedelta(days=30)
        )

    grid = grid_markup(client.get(URL).content.decode())

    assert grid.count("Paid placement") == FEATURED_CAP_PER_PAGE
    # And the page is still full — the cap reserves slots, it does not shrink the grid.
    assert grid.count("dir-practitioner__name") == 12


def test_the_paid_cap_the_page_promises_is_the_one_it_applies(client, listings):
    """The number in the copy comes from the same constant the query uses, so a
    change to one cannot leave the other lying."""
    from directory.services.search import FEATURED_CAP_PER_PAGE

    body = flat(client.get(URL).content.decode())

    assert f"No more than {FEATURED_CAP_PER_PAGE} featured listings appear together" in body


def test_the_page_discloses_that_position_can_be_paid_for(client, listings):
    """A per-card label satisfies CAP at the point of display; a listing set where
    payment affects POSITION also needs the arrangement stated (DMCCA 2024)."""
    body = flat(client.get(URL).content.decode())

    assert "Paid placement" in body
    assert "Paying changes position only" in body
    assert "none at all on the verification checks" in body


# ---------------------------------------------------------------------------
# Independence
# ---------------------------------------------------------------------------


def test_the_independence_notice_comes_before_the_grid(client, listings):
    """A visitor must not be able to form the impression these are Kiam's
    clinicians, and the sentence that prevents that has to be read first."""
    body = client.get(URL).content.decode()

    notice = body.index("are independent professionals")
    grid = body.index('class="dir-home-grid"')
    assert notice < grid


def test_the_independence_notice_is_verbatim(client):
    """docs/content-compliance.md §5, via the one partial. Not paraphrased."""
    body = flat(client.get(URL).content.decode())

    assert (
        "Practitioners listed in the Kiam Clinic Directory are independent professionals. "
        "They are not employed by, or part of the clinical team at, Kiam Clinic. Clients "
        "arrange appointments and payment directly with the practitioner." in body
    )


def test_the_page_never_offers_to_book_or_to_take_payment(client, listings):
    """Two of the three things docs/content-compliance.md §9 puts behind legal
    review. `booking_url` is populated on listings and rendered nowhere."""
    from directory.models import Practitioner

    Practitioner.objects.filter(pk=listings[0].pk).update(booking_url="https://booking.example.com/x")

    body = client.get(URL).content.decode().lower()

    assert "book an appointment" not in body
    assert "booking.example.com" not in body
    assert "book now" not in body


def test_the_page_does_not_offer_to_match_or_recommend_anybody(client, listings):
    """Triage and matching would move Kiam from introducer towards intermediary
    (CLAUDE.md, "What this project is") and neither may be built without legal
    review — so nothing on the home page may imply either exists."""
    body = flat(client.get(URL).content.decode())

    assert "does not choose a practitioner for you" in body
    assert "not a shortlist and it is not a recommendation" in body


def test_the_grid_is_described_as_a_sample_not_a_ranking(client, listings):
    body = flat(client.get(URL).content.decode())

    # The count comes from the grid rather than from a constant: Phase 5 could
    # never have seen "Twelve" be wrong, because `completeness` had no writer and
    # the grid was empty in production.
    assert "12 listings from the directory" in body
    assert "changes every day" in body
    assert "not a shortlist and it is not a recommendation" in body


def test_the_trust_strip_states_the_limits_of_the_badge(client):
    """The badge is a documents check. The home page is where most people meet the
    claim, so the limits are stated here rather than only one click away."""
    body = flat(client.get(URL).content.decode())

    assert "not a quality, competence or outcome claim" in body
    assert "not a recommendation" in body
    assert "/how-verification-works/" in client.get(URL).content.decode()


def test_no_verification_claim_is_unconditional(client, listings):
    """Both gate reviews caught this independently, and it is the worst kind of
    over-claim: a true-sounding sentence about somebody else's credentials.

    Publication does NOT require verification.
    `review.blocking_publication_reasons()` blocks only a restricted title with no
    verified registration, `is_verified` is a separate computed field, and Phase 3b's
    design deliberately keeps a listing PUBLISHED while its checks are reopened. So
    "before a listing goes live, Kiam Clinic checks…" was untrue of every unbadged
    listing — nine of twenty-eight on the review database.
    """
    body = flat(client.get(URL).content.decode())

    assert "Before a listing goes live" not in body
    assert "has checked their registration, qualifications and insurance before publishing" not in body

    # What it says instead: the badge carries the claim, and its absence is stated.
    # Tags stripped, because "Credentials checked" is wrapped in <strong> in the copy.
    text = flat(re.sub(r"<[^>]+>", " ", client.get(URL).content.decode()))
    assert "Where a listing shows “Credentials checked” and a date, Kiam Clinic had checked" in text
    assert "A listing without the badge has not been through those checks" in text
    assert "Being listed at all is not the same as being checked." in text


def test_an_unbadged_listing_can_appear_in_the_grid_which_is_why_the_copy_is_conditional(client, listings):
    """The condition is not hypothetical. `homepage_grid()` filters on `status` and
    `completeness`, never on `is_verified`, so the grid mixes badged and unbadged
    cards — which is correct, and is exactly why the copy above them cannot claim
    they were all checked."""
    from directory.services.search import homepage_grid

    # `listings` are published and complete but never verified.
    chosen = homepage_grid(12)

    assert chosen, "no grid to assert about"
    assert all(not p.is_verified for p in chosen)
    assert "Credentials checked" not in grid_markup(client.get(URL).content.decode())


def test_the_practitioner_cta_promises_no_message_relay(client):
    """There is no relay anywhere in this project, and `directory/views.py` says
    building one needs legal review first (docs/content-compliance.md §9). A CTA
    advertising it to prospective members is how a deferred decision gets made by
    accident."""
    body = flat(client.get(URL).content.decode())

    assert "sends enquiries straight to you" not in body
    assert "clients then contact you directly" in body


def test_how_it_works_ends_where_kiam_involvement_ends(client):
    body = flat(client.get(URL).content.decode())

    assert "Arrange it directly with them." in body
    assert "does not arrange appointments, takes no payment" in body


# ---------------------------------------------------------------------------
# Supporting sections and cross-links
# ---------------------------------------------------------------------------


def test_the_first_paragraph_answers_the_question_and_is_short(client):
    """docs/seo.md, "AEO / AI search": the first paragraph is what gets extracted,
    so it says what is listed, who is on it, and that contact is direct.

    The length matters as much as the content. The first draft put the whole
    plain-language explanation in the hero, and on a 375px viewport that was a
    nine-line serif paragraph between the visitor and the search box.
    """
    body = client.get(URL).content.decode()
    lede = body.split('class="ds-section-head__lede"', 1)[1].split("</p>", 1)[0]

    # It NAMES THE ENTITY. A fragment starting "Psychiatrists, psychologists, …"
    # reads as a list of nothing in particular once an answer engine lifts it out
    # of the page.
    assert flat(lede).lstrip(">").strip().startswith("The Kiam Clinic Directory lists independent")
    assert "You contact them directly" in flat(lede)
    assert len(flat(lede)) < 220, "the hero lede is long enough to push the search box off a phone"


def test_the_page_explains_what_the_directory_is_in_plain_language(client):
    """The brief's supporting section: one short paragraph, plain language, before
    anybody scrolls into twelve photographs."""
    body = flat(client.get(URL).content.decode())

    assert "What this directory is" in body
    assert "The Kiam Clinic Directory is a public list of independent mental-health practitioners" in body
    assert "There is nothing to sign up for and nothing to pay Kiam Clinic." in body


def test_what_this_directory_is_comes_before_the_grid(client, listings):
    body = client.get(URL).content.decode()

    assert body.index("What this directory is") < body.index('class="dir-home-grid"')


def test_the_browse_links_only_name_categories_that_have_listings(client, listings):
    """A "Trauma" link that leads to an empty result set is a dead end for the
    visitor and a thin page for the crawler."""
    SpecialityCategoryFactory(slug="empty-category", name="Nobody Does This")

    body = client.get(URL).content.decode()

    assert "Neurodevelopmental" in body
    assert "Nobody Does This" not in body


def test_a_town_needs_more_than_one_practitioner_to_be_offered(client, listings):
    """One practitioner behind "Practitioners in Guildford" is a dead end, and it
    also names a single person by their town."""
    PractitionerLocationFactory(practitioner=listings[0], city="Loneton")
    PractitionerLocationFactory(practitioner=listings[1], city="Twoford")
    PractitionerLocationFactory(practitioner=listings[2], city="Twoford")

    body = client.get(URL).content.decode()

    assert "Twoford" in body
    assert "Loneton" not in body


def test_a_town_only_used_for_the_radius_is_never_named(client, listings):
    """`is_public=False` exists so somebody can be findable by distance without
    publishing where they work — usually a home office. Naming it in a browse link
    publishes it by inference."""
    PractitionerLocationFactory(practitioner=listings[0], city="Hiddenham", private_address=True)
    PractitionerLocationFactory(practitioner=listings[1], city="Hiddenham", private_address=True)

    assert "Hiddenham" not in client.get(URL).content.decode()


def test_every_browse_count_is_rendered_with_a_unit(client, listings):
    """A bare "10" next to a link is a 1.3.1 information failure, and it is what the
    live page rendered when the browse payload's shape changed without a
    CACHE_VERSION bump. Asserted on the HTML, so it catches the symptom whatever the
    cause."""
    PractitionerLocationFactory(practitioner=listings[0], city="Epsom", postcode="KT18 5EP")
    PractitionerLocationFactory(practitioner=listings[1], city="Epsom", postcode="KT18 5EP")

    body = client.get(URL).content.decode()
    counts = re.findall(r'class="dir-browse__count">(.*?)</span>', body, re.S)

    assert counts, "no browse counts rendered at all"
    for count in counts:
        assert re.fullmatch(r"\s*\d+\s+(listed|based here)\s*", count), f"bare count: {count!r}"


def test_the_page_cross_links_to_the_main_site(client):
    """docs/seo.md: this subdomain does not inherit kiamclinic.com's authority, and
    the mitigation is deliberate cross-linking."""
    body = client.get(URL).content.decode()

    assert body.count("https://kiamclinic.com") >= 2  # the nav and the body section
    assert "Visit kiamclinic.com" in body


def test_the_cross_link_does_not_imply_the_listings_are_the_clinic(client):
    body = flat(client.get(URL).content.decode())

    assert "a separate thing from the practitioners listed here" in body


def test_the_page_has_a_for_practitioners_call_to_action(client):
    body = client.get(URL).content.decode()

    assert "Are you an independent practitioner?" in body
    assert 'href="/for-practitioners/"' in body
    # And it does not promise a self-serve sign-up that does not exist.
    assert "Listing is by invitation and membership" in body


def test_the_search_page_is_reachable_from_the_home_page(client):
    """A page nothing links to is not published, whatever the URLconf says."""
    assert 'href="/search/"' in client.get(URL).content.decode()


# ---------------------------------------------------------------------------
# SEO
# ---------------------------------------------------------------------------


def test_the_home_page_and_the_search_page_do_not_share_a_title(client, listings):
    """They shipped byte-identical <title>, og:title and <h1> — the two
    highest-priority indexable URLs on the subdomain, both in the sitemap, asking to
    be told apart by a description alone (docs/seo.md, "Every page"). Phase 5 made
    the duplication structural as well, by giving the home page the same hero search
    bar and the same disclaimer."""
    import re as _re

    def title_of(url):
        return _re.search(r"<title>(.*?)</title>", client.get(url).content.decode()).group(1)

    def h1_of(url):
        body = client.get(url).content.decode()
        return flat(_re.search(r"<h1[^>]*>(.*?)</h1>", body, _re.S).group(1))

    assert title_of("/") != title_of("/search/")
    assert h1_of("/") != h1_of("/search/")


def test_the_home_page_is_indexable_and_self_canonical(client, listings):
    body = client.get(URL).content.decode()

    assert 'name="robots"' not in body
    assert '<link rel="canonical" href="http://testserver/"' in body
    # Canonicals stay inside this subdomain (docs/multi-project-architecture.md §5).
    assert "kiamclinic.com/" not in body.split("<title>")[0]


def test_the_home_page_has_a_title_and_a_description(client):
    body = client.get(URL).content.decode()

    assert re.search(r"<title>.{10,}</title>", body)
    assert re.search(r'<meta name="description" content="[^"]{20,}"', body)


def test_the_home_page_has_exactly_one_h1(client, listings):
    assert client.get(URL).content.decode().count("<h1") == 1


def test_home_heading_order_never_skips_a_level(client, listings):
    main = client.get(URL).content.decode().split("<main", 1)[1].split("</main>", 1)[0]
    levels = [int(match) for match in re.findall(r"<h([1-6])[ >]", main)]

    assert levels[0] == 1
    for previous, current in zip(levels, levels[1:], strict=False):
        assert current <= previous + 1, f"h{previous} is followed by h{current}"


def test_the_home_page_emits_website_and_organization(client):
    """docs/seo.md fixes the pair for this page type."""
    body = client.get(URL).content.decode()
    raw = body.split('<script type="application/ld+json">')[1].split("</script>")[0]

    document = json.loads(raw)
    assert [node["@type"] for node in document["@graph"]] == ["WebSite", "Organization"]


def test_the_organization_node_carries_the_shared_nap(client, settings):
    """The same name, address and phone as kiamclinic.com and rooms — a NAP that
    disagrees across the three subdomains is worse than no NAP."""
    body = client.get(URL).content.decode()
    raw = body.split('<script type="application/ld+json">')[1].split("</script>")[0]
    organisation = json.loads(raw)["@graph"][1]

    assert organisation["name"] == "Kiam Clinic"
    assert organisation["telephone"] == "01372 660580"
    assert organisation["email"] == settings.KIAM_UI["CONTACT"]["email"]
    assert organisation["address"] == {
        "@type": "PostalAddress",
        "streetAddress": "13 Worple Road",
        "addressLocality": "Epsom",
        "addressRegion": "Surrey",
        "postalCode": "KT18 5EP",
        "addressCountry": "GB",
    }


def test_the_home_page_advertises_the_search_action(client):
    body = client.get(URL).content.decode()

    assert "SearchAction" in body
    assert "search_term_string" in body


def test_the_home_page_emits_no_ratings_and_no_itemlist(client, listings):
    """No reviews, and no `ItemList` of the twelve: each of them already has a
    `Person` node on their own profile, and the twelve change daily."""
    body = client.get(URL).content.decode()

    assert "AggregateRating" not in body
    assert "ItemList" not in body


def test_the_home_page_is_in_the_sitemap_at_the_top_priority(client):
    body = client.get("/sitemap.xml").content.decode()

    assert "<loc>https://testserver/</loc>" in body


def test_the_grid_links_to_real_profiles(client, listings):
    """The Phase 3 note that every published profile was an orphan linked only from
    sitemap.xml. This page is what ends that."""
    body = client.get(URL).content.decode()

    assert re.search(r'href="/p/listed-\d\d/"', body)


# ---------------------------------------------------------------------------
# Chrome and compliance carried by the page
# ---------------------------------------------------------------------------


def test_the_home_page_carries_the_crisis_signposting(client):
    """Asserted against the constant, so a reword cannot pass silently."""
    body = flat(client.get(URL).content.decode())

    assert nav.CRISIS_SIGNPOSTING in body


def test_the_home_page_names_no_prescription_only_medicine(client, listings):
    """docs/content-compliance.md §1, checked against the live blocklist. This page
    is authored markup, so the submission lint never sees it."""
    from directory.services.lint import pom_terms

    body = client.get(URL).content.decode().lower()
    for term in pom_terms():
        assert term.lower() not in body, f"the home page names {term}"


def test_no_template_comment_leaks_into_the_page(client, listings):
    """Django's `{# … #}` is a SINGLE-LINE comment. Four multi-line ones reached
    the rendered page at Phase 4, one where the radius label should have been."""
    body = client.get(URL).content.decode()

    assert "{#" not in body
    assert "#}" not in body
    assert "{% comment %}" not in body
    # The shapes of the mistake: a stray closing tag or an unrendered slot name.
    assert "actions_template" not in body
    assert "body_template" not in body
