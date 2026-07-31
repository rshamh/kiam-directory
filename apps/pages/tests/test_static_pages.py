"""The static pages.

Most of this module is golden rule #1 applied to eight URLs at once — every page
carries a title, a description, one `<h1>`, a self-referencing canonical inside
this subdomain, and JSON-LD that parses. Running it over the registry rather than
page by page means a page added in Phase 5 or 7 inherits the whole check for
free, and cannot be added without a meta description.

The rest is the copy that exists for a compliance reason and would otherwise be
edited away by somebody tidying: what the verification badge does not mean, and
that the legal pages do not pretend to be finished.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from apps.pages import content, nav

pytestmark = pytest.mark.django_db

ALL_PAGES = [f"pages:{page.name}" for page in content.STATIC_PAGES] + [
    f"pages:{name}" for name in content.FORM_PAGES
]


@pytest.fixture(params=ALL_PAGES)
def page_url(request):
    return reverse(request.param)


# ---------------------------------------------------------------------------
# Every page, every rule
# ---------------------------------------------------------------------------


def test_the_page_renders(client, page_url):
    assert client.get(page_url).status_code == 200


def test_the_page_has_a_title_and_a_description(client, page_url):
    body = client.get(page_url).content.decode()

    assert re.search(r"<title>.{10,}</title>", body)
    assert re.search(r'<meta name="description" content="[^"]{20,}"', body)


def test_the_page_has_exactly_one_h1(client, page_url):
    assert client.get(page_url).content.decode().count("<h1") == 1


def test_the_page_heading_order_never_skips_a_level(client, page_url):
    main = client.get(page_url).content.decode().split("<main", 1)[1].split("</main>", 1)[0]
    levels = [int(match) for match in re.findall(r"<h([1-6])[ >]", main)]

    assert levels[0] == 1
    for previous, current in zip(levels, levels[1:], strict=False):
        assert current <= previous + 1, f"h{previous} is followed by h{current}"


def test_the_page_canonical_is_self_referencing_and_on_this_subdomain(client, page_url):
    body = client.get(page_url).content.decode()

    assert f'<link rel="canonical" href="http://testserver{page_url}"' in body
    assert "kiamclinic.com/" not in body.split("<title>")[0]


def test_the_page_json_ld_parses_as_a_webpage(client, page_url):
    body = client.get(page_url).content.decode()
    raw = body.split('<script type="application/ld+json">')[1].split("</script>")[0]

    document = json.loads(raw)
    assert document["@context"] == "https://schema.org"
    assert [node["@type"] for node in document["@graph"]] == ["WebPage"]


def test_the_page_is_indexable(client, page_url):
    assert 'name="robots"' not in client.get(page_url).content.decode()


def test_the_page_is_in_the_sitemap(client, page_url):
    assert page_url in client.get("/sitemap.xml").content.decode()


def test_the_page_carries_the_crisis_signposting(client, page_url):
    """docs/content-compliance.md §7 — the footer of every public page."""
    body = client.get(page_url).content.decode()

    assert "call 111, or call 999 in an emergency" in body
    assert "Samaritans: 116 123" in body


def test_the_page_names_no_prescription_only_medicine(client, page_url):
    """docs/content-compliance.md §1, checked against the live blocklist."""
    from apps.directory.services.lint import pom_terms

    body = client.get(page_url).content.decode().lower()
    for term in pom_terms():
        assert term.lower() not in body, f"{page_url} names a prescription-only medicine"


# ---------------------------------------------------------------------------
# Copy that exists for a reason
# ---------------------------------------------------------------------------


def test_how_verification_works_states_the_limits_of_the_badge(client):
    """docs/verification-policy.md — the badge is a documents check, nothing more."""
    body = client.get(reverse("pages:how_verification_works")).content.decode()

    assert "Credentials checked" in body
    assert "not a quality, competence or outcome claim" in body.lower()
    assert "not a recommendation" in body.lower()
    assert "responsible for their\n        own practice" in body or "own practice" in body


def test_how_verification_works_explains_the_under_18_gate(client):
    body = client.get(reverse("pages:how_verification_works")).content.decode()

    assert "enhanced DBS" in body
    assert "adult work" in body
    assert "excluded from" in body


def test_the_legal_pages_do_not_pretend_to_be_finished(client):
    """CLAUDE.md — unsigned legal copy is flagged, never quietly published."""
    for name in ("terms", "privacy", "cookies"):
        body = client.get(reverse(f"pages:{name}")).content.decode()
        assert "still being prepared with our solicitors" in body
        assert "not the finished document" in body


def test_the_about_page_draws_the_boundary_in_both_directions(client):
    body = client.get(reverse("pages:about")).content.decode()

    assert "does not employ or supervise" in body
    assert "does not take payment" in body.lower() or "It does not take payment" in body
    assert "does not choose a practitioner for you" in body


def test_every_public_page_carries_the_independence_framing_somewhere(client, page_url):
    """The footer tagline is on every page; the full notice is on the profile."""
    body = client.get(page_url).content.decode()
    assert "independent mental-health practitioners" in body


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def test_every_footer_link_resolves(client, rf):
    """A footer link is on every page, so a stale one is a 404 in all of them."""
    footer = nav.footer(rf.get("/"))

    links = [link for column in footer["COLUMNS"] for link in column["links"]]
    links += footer["LEGAL_LINKS"]

    for link in links:
        if link["url"].startswith("/"):
            assert client.get(link["url"]).status_code == 200, link["url"]


def test_the_footer_carries_the_legal_pages_now_they_exist(rf):
    labels = {link["label"] for link in nav.footer(rf.get("/"))["LEGAL_LINKS"]}

    assert labels == {"Terms of use", "Privacy", "Cookies", "Accessibility"}


def test_every_nav_link_resolves(client, rf):
    for item in nav.nav_items(rf.get("/")):
        if item["url"].startswith("/"):
            assert client.get(item["url"]).status_code == 200, item["url"]
