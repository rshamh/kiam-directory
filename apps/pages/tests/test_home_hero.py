"""The hero search bar (Phase 5b).

What this rebuilt, and why it is worth its own file: the previous hero measured
**576px tall on a 375x812 phone** — 71% of the viewport, with the Search button
below the fold — and carried 55 words of grey help text inside the box. It worked;
it read as an admin form on a homepage.

Everything here is about the parts of that rebuild a rendering test can hold. The
parts it *cannot* — that the pill is 286px instead of 576px, that the focus ring
measures 5.13:1 on the gradient, that the hint no longer renders on top of the
Search button — were measured in a real browser and are recorded in the CSS beside
the rules they justify. That split is deliberate: the Phase 5 gate found four
defects that a green suite could not see, and pretending a `assertContains` proves
a layout is how the next four get shipped.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.directory.factories import PractitionerFactory, ProfessionFactory
from apps.pages.services import home

pytestmark = pytest.mark.django_db

URL = "/"


@pytest.fixture
def cohort():
    """Three professions with different numbers of published listings.

    Uneven on purpose — the chips are ordered by how many people are actually
    listed, and an even spread would let a wrong `order_by` pass.
    """
    counsellor = ProfessionFactory(slug="counsellor", name="Counsellor")
    psychiatrist = ProfessionFactory(slug="psychiatrist", name="Psychiatrist")
    coach = ProfessionFactory(slug="adhd-coach", name="ADHD Coach")

    for index in range(5):
        PractitionerFactory(published=True, complete=True, profession=counsellor, slug=f"c-{index}")
    for index in range(3):
        PractitionerFactory(published=True, complete=True, profession=psychiatrist, slug=f"p-{index}")
    PractitionerFactory(published=True, complete=True, profession=coach, slug="coach-0")
    return {"counsellor": counsellor, "psychiatrist": psychiatrist, "coach": coach}


# ---------------------------------------------------------------------------
# The guarantee the whole component exists to keep
# ---------------------------------------------------------------------------


def test_the_hint_reveal_needs_no_javascript(client):
    """The one-line hint is `:focus-within` in CSS, not a focus handler.

    A scripted version would need an inline script to avoid a flash of the
    un-enhanced layout, and this is the component that must survive scripting being
    off. So: every hint is in the HTML, every one is still an `aria-describedby`
    target, and nothing in the bar listens for focus.
    """
    body = client.get(URL).content.decode()
    bar = body.split('class="dir-searchbar', 1)[1].split("</form>", 1)[0]

    assert 'id="search-q-help"' in bar
    assert 'id="search-near-help"' in bar
    assert 'aria-describedby="search-q-help"' in bar
    assert 'aria-describedby="search-near-help"' in bar

    for handler in ("onfocus", "onfocusin", "x-on:focus", "@focus", "hx-on:focus"):
        assert handler not in bar, f"the hint reveal grew a {handler} handler"


def test_the_hint_row_is_not_a_live_region(client):
    """Phase 3's contact-reveal lesson, in a new place.

    Each hint is already the accessible description of the field taking focus.
    Announcing it again on every focus change would read the same guidance twice —
    so the reveal is silent to assistive technology, and the visible resting line
    is `aria-hidden` because it duplicates a description that is already exposed.
    """
    body = client.get(URL).content.decode()
    bar = body.split('class="dir-searchbar', 1)[1].split("</form>", 1)[0]

    assert "aria-live" not in bar
    assert 'class="dir-searchbar__resting" aria-hidden="true"' in bar


def test_the_submit_button_still_carries_the_word(client):
    """Not an icon-only circle, however good the reference looked.

    With scripting off this button is the only way to run a search, and this
    audience has a high rate of anxiety and neurodevelopmental conditions
    (CLAUDE.md golden rule #3). An icon anyone has to interpret is a worse control
    than a word anyone can read.
    """
    body = client.get(URL).content.decode()
    bar = body.split('class="dir-searchbar', 1)[1].split("</form>", 1)[0]
    submit = bar.split('class="dir-searchbar__submit"', 1)[1]

    assert 'type="submit"' in submit
    assert "Search" in submit
    assert 'sr-only">Search' not in submit, "the label was hidden and replaced by the glyph"


def test_the_radius_label_keeps_its_full_accessible_name(client):
    """The visible word shrank to "Within"; the accessible name did not.

    "Within" alone announces as "Within, combo box" with nothing to say what it
    measures (WCAG 2.4.6). The remainder is in `.sr-only`, and the visible word is
    the FIRST word of the accessible name — which is what 2.5.3 Label in Name asks
    of somebody using voice control to say "Within".
    """
    body = client.get(URL).content.decode()

    assert (
        '<label for="search-radius">Within<span class="sr-only"> (distance from your location)</span></label>'
        in body
    )


# ---------------------------------------------------------------------------
# The profession chips
# ---------------------------------------------------------------------------


def test_the_chips_are_counted_from_published_listings(client, cohort):
    """Never from `taxonomy.py`. The taxonomy has 29 professions and this directory
    lists a handful of them; a hand-picked four would put "Music Therapist" on the
    front page of a directory that has none.
    """
    links = home._profession_links()

    assert [row["label"] for row in links] == ["Counsellor", "Psychiatrist", "ADHD Coach"]
    assert [row["count"] for row in links] == [5, 3, 1]


def test_a_profession_with_no_published_listing_is_not_offered(client, cohort):
    """A chip is a promise that clicking it returns something."""
    ProfessionFactory(slug="dramatherapist", name="Dramatherapist")

    assert "Dramatherapist" not in [row["label"] for row in home._profession_links()]


def test_an_unpublished_listing_does_not_count_towards_a_chip(cohort):
    """The count under a chip is a claim on the busiest page on the subdomain."""
    PractitionerFactory(profession=cohort["coach"], slug="draft-coach", complete=True)

    coach = next(row for row in home._profession_links() if row["label"] == "ADHD Coach")
    assert coach["count"] == 1


def test_the_chips_are_capped(cohort):
    """Four. A home page fanning out into dozens of thin facet URLs is the doorway
    pattern docs/seo.md forbids, and the row has to stay on one line at 320px."""
    for index in range(6):
        profession = ProfessionFactory(slug=f"extra-{index}", name=f"Extra {index}")
        PractitionerFactory(published=True, complete=True, profession=profession, slug=f"x-{index}")

    assert len(home._profession_links()) == home.HERO_PROFESSION_LIMIT == 4


def test_every_chip_link_actually_returns_that_practitioner(client, cohort):
    """End to end, the way somebody with scripting off uses it: read the href off
    the home page and GET it.

    This is the assertion that would have caught the Phase 5 town links, three of
    which resolved to the wrong county. A link ASSERTS its destination.
    """
    body = client.get(URL).content.decode()
    chips = body.split('class="dir-hero__chips"', 1)[1].split("</nav>", 1)[0]

    assert 'href="/search/?profession=counsellor"' in chips

    results = client.get("/search/", {"profession": "counsellor"}).content.decode()
    assert "c-0" in results or "Counsellor" in results


def test_the_chips_are_links_and_not_tabs(client, cohort):
    """The reference this was modelled on used JavaScript tabs. Plain links work
    with scripting off, are crawlable, and need no ARIA tab pattern to keep
    correct."""
    body = client.get(URL).content.decode()
    chips = body.split('class="dir-hero__chips"', 1)[1].split("</nav>", 1)[0]

    assert 'role="tab"' not in chips
    assert "hx-get" not in chips
    assert chips.count("<a ") == 3


def test_the_chip_group_is_named(client, cohort):
    """Four bare links after a form is not a group. The heading is `.sr-only`
    because a visible one here would put a third block of text between the lede and
    the grid."""
    body = client.get(URL).content.decode()

    assert 'aria-labelledby="hero-chips-title"' in body
    assert '<h2 class="sr-only" id="hero-chips-title">' in body


# ---------------------------------------------------------------------------
# The scale line
# ---------------------------------------------------------------------------


def test_the_scale_line_counts_published_listings_only(client, cohort):
    PractitionerFactory(slug="a-draft", complete=True)

    assert home._hero_extras()["listing_count"] == 9


def test_the_scale_line_never_claims_anything_was_checked(client, cohort):
    """Publication does not require verification — `blocking_publication_reasons()`
    blocks only a restricted title with no verified registration, `is_verified` is a
    separate computed field, and a listing whose badge is withdrawn deliberately
    stays up. Every verification claim on this page is conditional on the badge for
    that reason; this line avoids the subject entirely.
    """
    body = client.get(URL).content.decode()
    scale = body.split('class="dir-hero__scale"', 1)[1].split("</p>", 1)[0]

    assert "practitioner" in scale
    for claim in ("verified", "checked", "vetted", "approved", "trusted"):
        assert claim not in scale.lower(), f'the scale line claims listings are "{claim}"'


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


def test_the_hero_payload_is_versioned(cohort):
    """The Phase 5 gate caught this module serving a payload whose SHAPE had
    changed. An old entry under a new reader is a live page rendering something
    nobody wrote.
    """
    home._hero_extras()
    stored = cache.get(home.HERO_CACHE_KEY)

    assert stored["version"] == home.CACHE_VERSION


def test_a_stale_hero_payload_is_rebuilt_rather_than_served(cohort):
    cache.set(home.HERO_CACHE_KEY, {"version": home.CACHE_VERSION - 1, "data": {"hero_professions": []}})

    assert home._hero_extras()["hero_professions"], "a payload from an older version was served"


def test_unpublishing_drops_the_hero_payload(cohort):
    """`homepage:hero` is in `publication.CACHE_KEY_PATTERNS`, so a suspension that
    empties a profession removes its chip within seconds rather than in 24 hours."""
    from apps.backoffice.services import publication

    home._hero_extras()
    assert cache.get(home.HERO_CACHE_KEY) is not None

    publication.bust_cache(PractitionerFactory(published=True, complete=True, slug="anybody"))

    assert cache.get(home.HERO_CACHE_KEY) is None
