"""The public profile — resolution, sections, and the SEO head.

Two themes run through this module:

* **Only PUBLISHED resolves, and everything else fails identically.** A draft, a
  suspension and a slug nobody has ever used all produce the same 404. A
  different status code or a different page for the suspended case would tell the
  public that a named person was taken down.
* **Golden rule #1 is checked on a rendered page**, not on the helper that builds
  the tag. A JSON-LD builder with no consumer proves nothing.
"""

from __future__ import annotations

import json
import re

import pytest
from django.contrib.gis.geos import Point

from apps.directory.factories import (
    ApproachFactory,
    LanguageFactory,
    PractitionerFactory,
    PractitionerLocationFactory,
    SpecialityFactory,
)
from apps.directory.models import Practitioner, PublicationStatus, Qualification, Registration

pytestmark = pytest.mark.django_db


@pytest.fixture
def published():
    practitioner = PractitionerFactory(
        published=True,
        slug="jane-example",
        full_name="Jane Example",
        display_title="Dr",
        post_nominals="MBBS MRCPsych",
        pronouns="she/her",
        intro="I work with adults who have spent years being told they are simply disorganised.",
        services="Assessment, review and ongoing support.",
        fee_min=18000,
        fee_max=25000,
        online_coverage="UK-wide",
        specialities=[SpecialityFactory(slug="adult-adhd", name="Adult ADHD assessment")],
        approaches=[ApproachFactory(slug="cbt", name="CBT")],
        languages=[LanguageFactory(code="en", name="English")],
    )
    PractitionerLocationFactory(
        practitioner=practitioner,
        label="Epsom consulting rooms",
        address_line1="13 Worple Road",
        city="Epsom",
        county="Surrey",
        postcode="KT18 5EP",
        step_free_access=True,
        hearing_loop=True,
    )
    Qualification.objects.create(
        practitioner=practitioner, title="MBBS", institution="King's College London", year=2009
    )
    Registration.objects.create(
        practitioner=practitioner,
        body="GMC",
        registration_no="1234567",
        verified=True,
        register_url="https://www.gmc-uk.org/example",
    )
    return practitioner


def _get(client, practitioner):
    return client.get(f"/p/{practitioner.slug}/")


def _main(response) -> str:
    """The page's own content, without the chrome around it."""
    return response.content.decode().split("<main", 1)[1].split("</main>", 1)[0]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_published_profile_renders(client, published):
    response = _get(client, published)

    assert response.status_code == 200
    assert "Jane Example" in response.content.decode()


@pytest.mark.parametrize(
    "status",
    [
        PublicationStatus.DRAFT,
        PublicationStatus.SUBMITTED,
        PublicationStatus.IN_REVIEW,
        PublicationStatus.CHANGES_REQUESTED,
        PublicationStatus.APPROVED,
        PublicationStatus.SUSPENDED,
        PublicationStatus.UNPUBLISHED,
        PublicationStatus.REMOVED,
    ],
)
def test_nothing_but_published_resolves(client, published, status):
    Practitioner.objects.filter(pk=published.pk).update(status=status)

    assert _get(client, published).status_code == 404


def test_a_suspended_profile_is_indistinguishable_from_one_that_never_existed(client, published):
    """Same status, same page. The reason lives in the audit log, for staff.

    Compared on `<main>` rather than on the whole document: every response
    carries a fresh CSRF token and echoes its own URL into the canonical, and
    neither says anything about whether a listing exists. What has to be
    identical is what a reader sees.
    """
    Practitioner.objects.filter(pk=published.pk).update(
        status=PublicationStatus.SUSPENDED, suspend_reason="Registration under investigation"
    )

    suspended = _get(client, published)
    never_existed = client.get("/p/no-such-person-has-ever-been-listed/")

    assert suspended.status_code == never_existed.status_code == 404
    assert _main(suspended) == _main(never_existed)

    body = suspended.content.decode()
    assert "suspend" not in body.lower()
    assert "investigation" not in body.lower()
    assert "Jane Example" not in body


def test_the_profile_is_get_only(client, published):
    assert client.post(f"/p/{published.slug}/").status_code == 405


# ---------------------------------------------------------------------------
# What the page shows
# ---------------------------------------------------------------------------


def test_the_profile_shows_every_section(client, published):
    body = _get(client, published).content.decode()

    assert "Dr Jane Example" in body
    assert "MBBS MRCPsych" in body
    assert "she/her" in body
    assert "Adult ADHD assessment" in body
    assert "CBT" in body
    assert "English" in body
    assert "King&#x27;s College London" in body or "King's College London" in body
    assert "GMC" in body
    assert "1234567" in body
    assert "Epsom" in body
    assert "KT18 5EP" in body
    assert "Step-free access" in body
    assert "Hearing loop" in body
    assert "£180–£250 per session" in body
    assert "Currently accepting new clients" in body
    assert "last updated" in body


def test_a_private_address_is_not_published(client, published):
    """`is_public=False` means "use it for the search radius, do not print it"."""
    PractitionerLocationFactory(
        practitioner=published,
        city="Guildford",
        address_line1="221B Home Street",
        postcode="GU1 3UY",
        is_public=False,
        is_primary=False,
        geo=Point(-0.5704, 51.2362, srid=4326),
    )

    body = _get(client, published).content.decode()

    assert "221B Home Street" not in body
    assert "GU1 3UY" not in body


def test_the_independence_notice_is_verbatim_and_not_a_footnote(client, published):
    """docs/content-compliance.md §5 — every profile page, word for word."""
    body = " ".join(_get(client, published).content.decode().split())

    assert (
        "Practitioners listed in the Kiam Clinic Directory are independent professionals. "
        "They are not employed by, or part of the clinical team at, Kiam Clinic. Clients "
        "arrange appointments and payment directly with the practitioner." in body
    )

    # Above the practitioner's own words, not buried under them.
    assert body.index("independent professionals") < body.index("simply disorganised")


def test_the_first_paragraph_answers_the_question(client, published):
    """docs/seo.md, "AEO / AI search" — this is the sentence that gets extracted."""
    body = _get(client, published).content.decode()

    assert "Dr Jane Example is an independent" in body
    assert "Adult ADHD assessment" in body
    assert "in Epsom" in body
    assert "in person and online" in body


def test_the_badge_shows_its_date_and_links_to_its_limits(client):
    practitioner = PractitionerFactory(published=True, slug="verified-example", verified=True)

    body = _get(client, practitioner).content.decode()

    assert "Credentials checked" in body
    assert "/how-verification-works/" in body


def test_an_unverified_listing_shows_no_badge(client, published):
    body = _get(client, published).content.decode()
    assert "Credentials checked" not in body


def test_a_listing_with_almost_nothing_on_it_still_renders(client):
    """`profession` is nullable and every taxonomy relation can be empty.

    A profile that renders only for the fully-populated case is a 500 waiting for
    the first sparse listing — and the sparse one is the one somebody is midway
    through filling in.
    """
    bare = PractitionerFactory(
        published=True,
        slug="sparse-example",
        full_name="Sam Minimal",
        display_title="",
        profession=None,
        intro="",
        services="",
    )

    response = _get(client, bare)
    body = response.content.decode()

    assert response.status_code == 200
    assert "Sam Minimal is an independent practitioner" in body
    assert "does not name its areas of practice yet" in body
    assert "does not publish contact details" in body
    assert "Fees are not published on this listing" in body
    assert body.count("<h1") == 1


def test_the_fee_note_says_kiam_takes_no_payment(client, published):
    body = _get(client, published).content.decode()
    assert "Kiam Clinic\n      takes no payment" in body or "takes no payment" in body


# ---------------------------------------------------------------------------
# SEO head (golden rule #1)
# ---------------------------------------------------------------------------


def test_the_profile_carries_the_full_seo_head(client, published):
    body = _get(client, published).content.decode()

    assert "<title>Dr Jane Example" in body
    assert re.search(r'<meta name="description" content="[^"]{20,}"', body)
    assert f'<link rel="canonical" href="http://testserver/p/{published.slug}/"' in body
    assert 'property="og:type" content="profile"' in body
    assert 'property="og:title"' in body
    assert 'property="og:description"' in body
    assert 'property="og:image"' in body
    assert 'name="twitter:card"' in body


def test_the_og_image_is_absolute_when_storage_already_is(client, published, monkeypatch):
    """Production serves headshots from S3, where `.url` is already absolute.

    The template used to concatenate `scheme://host` onto it unconditionally,
    producing "https://directory.kiamclinic.comhttps://bucket.s3…/x.jpg" on every
    profile with a photo. Correct in dev and test, broken in production, and
    invisible to the suite because both use FileSystemStorage — so this test
    makes storage behave the way production's does.
    """
    # Patched on FieldFile rather than on a storage class: `.url` is what both
    # the view and the template call, and going through it keeps the test true
    # whichever backend a given environment configures (InMemoryStorage here,
    # FileSystemStorage in dev, S3 in production).
    from django.db.models.fields.files import FieldFile

    monkeypatch.setattr(
        FieldFile,
        "url",
        property(lambda self: f"https://bucket.s3.amazonaws.com/{self.name}"),
    )
    Practitioner.objects.filter(pk=published.pk).update(headshot="headshots/jane.jpg")

    body = _get(client, published).content.decode()

    assert 'content="https://bucket.s3.amazonaws.com/headshots/jane.jpg"' in body
    assert "testserverhttps://" not in body


def test_the_meta_description_fits_in_a_search_snippet(client):
    """The budget has to cover the call to action, not be spent before it.

    Truncating the answer sentence at 155 and *then* appending 56 more characters
    produced 212-character descriptions that Google cut mid-sentence, stranding a
    stray ellipsis in the middle of the snippet.
    """
    from apps.directory.services import profile as profile_service

    wordy = PractitionerFactory(
        published=True,
        slug="wordy-example",
        full_name="Alexandra Fotherington-Smythe",
        display_title="Dr",
        specialities=[
            SpecialityFactory(slug="s1", name="Adult ADHD assessment"),
            SpecialityFactory(slug="s2", name="Autism assessment for adults"),
            SpecialityFactory(slug="s3", name="Medication review and titration"),
        ],
    )
    PractitionerLocationFactory(practitioner=wordy, city="Epsom")
    PractitionerLocationFactory(practitioner=wordy, city="Guildford", is_primary=False)

    description = profile_service.meta_description(wordy)

    assert len(description) <= profile_service.META_DESCRIPTION_LIMIT
    assert "…" not in description.rstrip("…")[:-1]


def test_a_short_listing_still_gets_the_call_to_action(client, published):
    from apps.directory.services import profile as profile_service

    brief = PractitionerFactory(published=True, slug="brief-example", full_name="Jo Short")

    assert profile_service.META_DESCRIPTION_SUFFIX in profile_service.meta_description(brief)


def test_the_profile_has_exactly_one_h1(client, published):
    assert _get(client, published).content.decode().count("<h1") == 1


def test_the_profile_heading_order_never_skips_a_level(client, published):
    """Scoped to <main>: the header and footer chrome are kiam-ui's to answer for."""
    body = _get(client, published).content.decode()
    main = body.split("<main", 1)[1].split("</main>", 1)[0]
    levels = [int(match) for match in re.findall(r"<h([1-6])[ >]", main)]

    assert levels[0] == 1
    for previous, current in zip(levels, levels[1:], strict=False):
        assert current <= previous + 1, f"h{previous} is followed by h{current}"


def test_the_profile_is_indexable(client, published):
    assert 'name="robots"' not in _get(client, published).content.decode()


def test_the_profile_emits_person_and_profilepage(client, published):
    body = _get(client, published).content.decode()
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, flags=re.DOTALL)
    graphs = [json.loads(block) for block in blocks]

    profile_graph = next(g for g in graphs if "@graph" in g)
    types = {node["@type"] for node in profile_graph["@graph"]}
    assert types == {"Person", "ProfilePage"}

    person = next(node for node in profile_graph["@graph"] if node["@type"] == "Person")
    assert person["name"] == "Dr Jane Example"
    assert person["knowsLanguage"] == ["English"]
    assert "Adult ADHD assessment" in person["knowsAbout"]


def test_the_profile_emits_a_breadcrumb_list(client, published):
    body = _get(client, published).content.decode()
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, flags=re.DOTALL)
    graphs = [json.loads(block) for block in blocks]

    breadcrumbs = next(g for g in graphs if g.get("@type") == "BreadcrumbList")
    labels = [item["name"] for item in breadcrumbs["itemListElement"]]
    assert labels == ["Home", "Dr Jane Example"]
    for item in breadcrumbs["itemListElement"]:
        assert item["item"].startswith("https://")


def test_the_profile_json_ld_never_marks_a_practitioner_as_a_clinic(client, published):
    """docs/seo.md — Physician/MedicalBusiness misrepresents the relationship."""
    body = _get(client, published).content.decode()

    assert "Physician" not in body
    assert "MedicalBusiness" not in body
    assert "AggregateRating" not in body
    # And nothing claiming Kiam employs them.
    assert "worksFor" not in body
    assert "affiliation" not in body


def test_the_profile_json_ld_does_not_leak_the_contact_details(client, published):
    """The reveal is pointless if the address is in a machine-readable envelope."""
    # Not the clinic's own number — that one is in the footer of every page, and
    # asserting on it would test the fixture rather than the leak.
    Practitioner.objects.filter(pk=published.pk).update(
        public_email="jane@example.com",
        public_phone="07700 900123",
        public_website="https://jane.example.com",
    )

    body = _get(client, published).content.decode()

    assert "jane@example.com" not in body
    assert "07700 900123" not in body
    assert "jane.example.com" not in body
    assert "sameAs" not in body


# ---------------------------------------------------------------------------
# Sitemap
# ---------------------------------------------------------------------------


def test_a_published_profile_is_in_the_sitemap(client, published):
    body = client.get("/sitemap.xml").content.decode()
    assert f"/p/{published.slug}/" in body


def test_an_unpublished_profile_is_not_in_the_sitemap(client, published):
    Practitioner.objects.filter(pk=published.pk).update(status=PublicationStatus.SUSPENDED)

    body = client.get("/sitemap.xml").content.decode()
    assert f"/p/{published.slug}/" not in body
