"""The SEO base: robots, llms.txt, sitemap, JSON-LD, and the head tags.

Golden rule #1 says every page carries title, meta, canonical, OG and JSON-LD.
This is where that stops being an intention.
"""

from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from seo import jsonld

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# robots.txt — this subdomain's own
# ---------------------------------------------------------------------------


def test_robots_txt_is_served_as_plain_text(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")


def test_robots_txt_points_at_this_subdomains_sitemap(client):
    body = client.get("/robots.txt").content.decode()

    assert "Sitemap: http://testserver/sitemap.xml" in body
    # Never another subdomain's.
    assert "rooms.kiamclinic.com" not in body


def test_robots_txt_disallows_the_account_and_staff_areas(client):
    body = client.get("/robots.txt").content.decode()

    assert "Disallow: /accounts/" in body
    assert "Disallow: /staff-console/" in body


def test_robots_txt_does_not_block_search(client):
    """Facet URLs must stay crawlable so their noindex is actually read."""
    body = client.get("/robots.txt").content.decode()
    assert "Disallow: /search" not in body


# ---------------------------------------------------------------------------
# llms.txt
# ---------------------------------------------------------------------------


def test_llms_txt_is_served(client):
    response = client.get("/llms.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")


def test_llms_txt_states_the_independence_relationship(client):
    """An answer engine summarising this site must not call them Kiam clinicians."""
    body = client.get("/llms.txt").content.decode()

    assert "independent professionals" in body
    assert "not employed by, or part of the clinical team at, Kiam Clinic" in body
    assert "publisher and introducer only" in body


def test_llms_txt_says_it_is_not_a_crisis_service(client):
    body = client.get("/llms.txt").content.decode()

    assert "not an emergency or crisis service" in body
    assert "116 123" in body


# ---------------------------------------------------------------------------
# sitemap.xml
# ---------------------------------------------------------------------------


def test_sitemap_is_served_and_contains_the_home_page(client):
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert b"<urlset" in response.content
    assert b"<loc>" in response.content


def test_sitemap_contains_no_search_urls(client):
    """Faceted URLs are noindex; a sitemap entry claims the opposite."""
    body = client.get("/sitemap.xml").content.decode()
    assert "/search/" not in body


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------


def test_home_graph_has_website_and_organization():
    document = jsonld.graph(jsonld.website(), jsonld.organisation())
    types = {node["@type"] for node in document["@graph"]}

    assert types == {"WebSite", "Organization"}


def test_the_shared_nap_is_emitted():
    org = jsonld.organisation()

    assert org["address"]["streetAddress"] == "13 Worple Road"
    assert org["address"]["postalCode"] == "KT18 5EP"
    assert org["telephone"] == "01372 660580"


def test_aggregate_rating_is_rejected():
    """There are no reviews and there never will be (docs/seo.md)."""
    with pytest.raises(jsonld.ProhibitedStructuredData):
        jsonld.graph({"@type": "Person", "aggregateRating": {"@type": "AggregateRating"}})


def test_physician_is_rejected():
    """Listed practitioners are independent, not a Kiam clinic location."""
    with pytest.raises(jsonld.ProhibitedStructuredData):
        jsonld.graph({"@type": "Physician", "name": "Dr Example"})


def test_medical_business_is_rejected():
    with pytest.raises(jsonld.ProhibitedStructuredData):
        jsonld.graph({"@type": ["Person", "MedicalBusiness"]})


def test_prohibited_types_are_caught_when_nested():
    with pytest.raises(jsonld.ProhibitedStructuredData):
        jsonld.graph({"@type": "WebPage", "about": {"mainEntity": {"@type": "Review"}}})


def test_render_escapes_a_closing_script_tag():
    payload = jsonld.render(jsonld.graph({"@type": "WebPage", "name": "</script><b>x"}))
    assert "</script><b>" not in payload.split('type="application/ld+json">')[1]


def test_absolute_urls_stay_on_this_subdomain(settings):
    settings.SITE_BASE_URL = "https://directory.kiamclinic.test"
    assert jsonld.absolute("/p/example/") == "https://directory.kiamclinic.test/p/example/"


# ---------------------------------------------------------------------------
# Head tags on a real page
# ---------------------------------------------------------------------------


def test_the_home_page_carries_the_full_seo_head(client):
    body = client.get(reverse("pages:home")).content.decode()

    assert "<title>" in body
    assert re.search(r'<meta name="description" content="[^"]{20,}"', body)
    assert '<link rel="canonical" href="http://testserver/"' in body
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:image"' in body
    assert 'name="twitter:card"' in body
    assert 'type="application/ld+json"' in body


def test_the_home_pages_json_ld_parses(client):
    body = client.get(reverse("pages:home")).content.decode()
    raw = body.split('<script type="application/ld+json">')[1].split("</script>")[0]

    document = json.loads(raw)
    assert document["@context"] == "https://schema.org"
    assert {node["@type"] for node in document["@graph"]} == {"WebSite", "Organization"}


def test_the_home_page_has_exactly_one_h1(client):
    body = client.get(reverse("pages:home")).content.decode()
    assert body.count("<h1") == 1


def test_the_home_page_is_indexable(client):
    body = client.get(reverse("pages:home")).content.decode()
    assert 'name="robots"' not in body


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


def test_404_renders_the_custom_page(client):
    response = client.get("/no-such-page-exists/")

    assert response.status_code == 404
    assert b"We couldn&#x27;t find that page" in response.content or b"couldn" in response.content


def test_404_is_noindex(client):
    body = client.get("/no-such-page-exists/").content
    assert b'content="noindex, follow"' in body


def test_500_renders_without_any_context(client):
    """It must render with no database, no cache and no context processors."""
    from django.template.loader import render_to_string

    html = render_to_string("500.html")

    assert "Something went wrong" in html
    assert "116 123" in html  # crisis signposting survives an outage
