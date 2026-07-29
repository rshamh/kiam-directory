"""Daily counters.

Counters only — no per-visitor rows. That is what keeps this outside the cookie
consent burden and out of scope for a subject access request, so the test that
matters most here is the one asserting there is nothing to identify.
"""

from __future__ import annotations

import pytest
from django.utils import timezone

from directory.factories import PractitionerFactory
from directory.models import DailyMetric
from directory.services import metrics
from directory.services import profile as profile_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def published():
    return PractitionerFactory(published=True, slug="counted-example", public_email="a@example.com")


def test_the_channel_maps_agree():
    """Two dicts, three keys, one routing bug waiting to happen.

    `profile.CHANNELS` decides which buttons render; `metrics.FIELD_BY_CHANNEL`
    decides which column moves. A channel in one and not the other either renders
    a button that counts nothing or counts a column nobody can reach.
    """
    assert set(metrics.FIELD_BY_CHANNEL) == set(profile_service.CHANNELS)


def test_viewing_a_profile_counts_a_view(client, published):
    client.get(f"/p/{published.slug}/")

    assert DailyMetric.objects.get(practitioner=published).profile_views == 1


def test_views_accumulate_on_one_row_per_day(client, published):
    client.get(f"/p/{published.slug}/")
    client.get(f"/p/{published.slug}/")

    metric = DailyMetric.objects.get(practitioner=published)
    assert metric.profile_views == 2
    assert metric.date == timezone.localdate()


def test_a_crawler_is_not_counted_as_a_visitor(client, published):
    """Otherwise the first month's numbers are mostly Googlebot."""
    client.get(f"/p/{published.slug}/", HTTP_USER_AGENT="Mozilla/5.0 (compatible; Googlebot/2.1)")

    assert not DailyMetric.objects.filter(practitioner=published).exists()


def test_a_404_counts_nothing(client, published):
    client.get("/p/not-a-listing/")

    assert not DailyMetric.objects.exists()


def test_a_metric_holds_nothing_about_the_visitor():
    """No IP, no session, no user agent. There is nothing here to DSAR."""
    columns = {field.name for field in DailyMetric._meta.get_fields()}

    assert not columns & {"ip", "session", "user_agent", "user", "visitor", "referrer"}


def test_an_unknown_channel_invents_no_column(published):
    metrics.record_reveal(published, "carrier-pigeon")

    assert not DailyMetric.objects.exists()


def test_a_failed_metric_write_never_breaks_the_page(client, published, monkeypatch):
    """The counter is bookkeeping; the profile is the product."""

    def explode(*args, **kwargs):
        raise RuntimeError("database is having a moment")

    monkeypatch.setattr(DailyMetric.objects, "get_or_create", explode)

    assert client.get(f"/p/{published.slug}/").status_code == 200
