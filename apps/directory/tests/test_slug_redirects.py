"""Slug redirects.

A published profile's URL is the one thing about a listing that other people own
copies of — a practitioner's own site links to it, a referral email quotes it, a
search engine has indexed it. So a slug change leaves a 301 behind rather than
breaking all of them, and it does so automatically: a redirect that depends on
somebody remembering to add a row is a redirect that will not exist the one time
it matters.
"""

from __future__ import annotations

import pytest

from apps.directory.factories import PractitionerFactory
from apps.directory.models import Practitioner, PublicationStatus, SlugRedirect

pytestmark = pytest.mark.django_db


@pytest.fixture
def published():
    return PractitionerFactory(published=True, slug="jane-example", full_name="Jane Example")


# ---------------------------------------------------------------------------
# The row is written automatically
# ---------------------------------------------------------------------------


def test_renaming_a_published_listing_writes_a_redirect(published):
    published.slug = "jane-married-name"
    published.save()

    redirect = SlugRedirect.objects.get(old_slug="jane-example")
    assert redirect.practitioner_id == published.pk


def test_renaming_a_listing_that_was_never_published_writes_nothing(db):
    """No inbound links exist yet, so there is nothing to preserve."""
    draft = PractitionerFactory(slug="draft-example")
    draft.slug = "draft-renamed"
    draft.save()

    assert not SlugRedirect.objects.exists()


def test_a_suspended_listing_still_gets_a_redirect_row(published):
    """`published_at` is the test, not the current status.

    A suspended listing's old URL is still out in the world, and the listing may
    be back next week.
    """
    Practitioner.objects.filter(pk=published.pk).update(status=PublicationStatus.SUSPENDED)
    published.refresh_from_db()

    published.slug = "jane-corrected"
    published.save()

    assert SlugRedirect.objects.filter(old_slug="jane-example").exists()


def test_saving_without_changing_the_slug_writes_nothing(published):
    published.intro = "Updated."
    published.save()

    assert not SlugRedirect.objects.exists()


def test_two_renames_leave_two_working_redirects(published):
    published.slug = "second"
    published.save()
    published.slug = "third"
    published.save()

    assert set(SlugRedirect.objects.values_list("old_slug", flat=True)) == {
        "jane-example",
        "second",
    }
    # Both point at the practitioner, not at each other — no chain to follow.
    assert all(r.practitioner_id == published.pk for r in SlugRedirect.objects.all())


def test_a_slug_that_comes_back_into_use_loses_its_stale_redirect(published):
    """A -> B -> A must not leave a row claiming A is an old name for A."""
    published.slug = "second"
    published.save()
    published.slug = "jane-example"
    published.save()

    assert not SlugRedirect.objects.filter(old_slug="jane-example").exists()
    assert SlugRedirect.objects.filter(old_slug="second").exists()


# ---------------------------------------------------------------------------
# The redirect resolves
# ---------------------------------------------------------------------------


def test_an_old_slug_redirects_permanently_to_the_current_one(client, published):
    published.slug = "jane-married-name"
    published.save()

    response = client.get("/p/jane-example/")

    assert response.status_code == 301
    assert response["Location"] == "/p/jane-married-name/"


def test_the_redirect_lands_on_the_profile(client, published):
    published.slug = "jane-married-name"
    published.save()

    response = client.get("/p/jane-example/", follow=True)

    assert response.status_code == 200
    assert "Jane Example" in response.content.decode()


def test_a_live_slug_always_beats_a_redirect_row(client, published):
    """A retired slug that another listing later takes must not bounce anyone."""
    published.slug = "jane-married-name"
    published.save()

    reuser = PractitionerFactory(published=True, slug="jane-example", full_name="Someone Else")

    response = client.get("/p/jane-example/")

    assert response.status_code == 200
    assert reuser.full_name in response.content.decode()


def test_an_old_slug_for_a_listing_that_is_no_longer_public_is_a_404(client, published):
    """Not a 301 into a 404 — that would confirm the listing once existed."""
    published.slug = "jane-married-name"
    published.save()
    Practitioner.objects.filter(pk=published.pk).update(status=PublicationStatus.SUSPENDED)

    assert client.get("/p/jane-example/").status_code == 404


def test_an_old_slug_is_not_in_the_sitemap(client, published):
    """The sitemap claims a URL is worth indexing; a redirect is not."""
    published.slug = "jane-married-name"
    published.save()

    body = client.get("/sitemap.xml").content.decode()

    assert "/p/jane-married-name/" in body
    assert "/p/jane-example/" not in body
