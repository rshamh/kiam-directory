"""Magic-link issue and consume.

The interesting assertions are the negative ones: what the flow must *not* do.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import LoginToken
from apps.accounts.services import magic_link, ratelimit

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Token handling
# ---------------------------------------------------------------------------


def test_raw_token_is_never_stored(practitioner):
    token, raw = magic_link.issue(practitioner)

    assert token.token_hash != raw
    assert token.token_hash == magic_link.hash_token(raw)
    assert len(token.token_hash) == 64

    # The raw value must not appear in any column of the row.
    row = LoginToken.objects.filter(pk=token.pk).values().get()
    assert raw not in str(row)


def test_raw_token_is_never_logged(practitioner, caplog):
    with caplog.at_level(logging.DEBUG, logger="accounts"):
        _, raw = magic_link.issue(practitioner)
        magic_link.consume(raw)

    logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
    assert raw not in logged
    assert magic_link.hash_token(raw) not in logged


def test_consume_returns_the_user(practitioner):
    _, raw = magic_link.issue(practitioner)
    assert magic_link.consume(raw) == practitioner


def test_a_token_works_exactly_once(practitioner):
    _, raw = magic_link.issue(practitioner)

    assert magic_link.consume(raw) == practitioner
    assert magic_link.consume(raw) is None


def test_an_expired_token_is_refused(practitioner):
    token, raw = magic_link.issue(practitioner)
    token.expires_at = timezone.now() - timedelta(seconds=1)
    token.save(update_fields=["expires_at"])

    assert magic_link.consume(raw) is None


def test_an_unknown_token_is_refused(practitioner):
    magic_link.issue(practitioner)
    assert magic_link.consume("not-a-real-token") is None
    assert magic_link.consume("") is None


def test_redeeming_the_newest_link_invalidates_older_ones(practitioner):
    """A forwarded or leaked earlier email must not stay live for its full TTL."""
    _, first = magic_link.issue(practitioner)
    _, second = magic_link.issue(practitioner)

    assert magic_link.consume(second) == practitioner
    assert magic_link.consume(first) is None


def test_a_deactivated_user_cannot_consume(practitioner):
    _, raw = magic_link.issue(practitioner)
    practitioner.is_active = False
    practitioner.save(update_fields=["is_active"])

    assert magic_link.consume(raw) is None


# ---------------------------------------------------------------------------
# Requesting a link
# ---------------------------------------------------------------------------


def test_request_sends_a_link_to_a_known_address(practitioner):
    magic_link.request_link(practitioner.email)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [practitioner.email]
    assert LoginToken.objects.filter(user=practitioner).count() == 1


def test_request_for_an_unknown_address_sends_nothing_and_says_nothing(db):
    result = magic_link.request_link("nobody@example.com")

    assert result is None
    assert mail.outbox == []
    assert LoginToken.objects.count() == 0


def test_request_for_a_deactivated_account_sends_nothing(user_factory):
    user = user_factory("dormant@example.com")
    user.is_active = False
    user.save(update_fields=["is_active"])

    magic_link.request_link(user.email)

    assert mail.outbox == []


def test_address_is_matched_case_insensitively(practitioner):
    magic_link.request_link("  PRACTITIONER@Example.COM  ")

    assert len(mail.outbox) == 1


def test_the_emailed_link_actually_consumes(practitioner, client):
    magic_link.request_link(practitioner.email)
    body = mail.outbox[0].body

    raw = body.split("/accounts/login/")[1].split("\n")[0].strip().rstrip("/")
    assert magic_link.consume(raw) == practitioner


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_requests_are_rate_limited_per_email(practitioner, settings):
    settings.MAGIC_LINK_MAX_PER_EMAIL = 2

    magic_link.request_link(practitioner.email, ip="203.0.113.1")
    magic_link.request_link(practitioner.email, ip="203.0.113.2")

    with pytest.raises(magic_link.RateLimited):
        magic_link.request_link(practitioner.email, ip="203.0.113.3")

    assert len(mail.outbox) == 2


def test_requests_are_rate_limited_per_ip(user_factory, settings):
    settings.MAGIC_LINK_MAX_PER_IP = 2

    for i in range(2):
        user_factory(f"person{i}@example.com")
        magic_link.request_link(f"person{i}@example.com", ip="198.51.100.7")

    user_factory("third@example.com")
    with pytest.raises(magic_link.RateLimited):
        magic_link.request_link("third@example.com", ip="198.51.100.7")


def test_the_email_limit_applies_to_addresses_with_no_account(db, settings):
    """Otherwise the limiter is an oracle: unlimited tries mean no account."""
    settings.MAGIC_LINK_MAX_PER_EMAIL = 1

    magic_link.request_link("nobody@example.com")
    with pytest.raises(magic_link.RateLimited):
        magic_link.request_link("nobody@example.com")


def test_limits_are_per_key_not_global(user_factory, settings):
    settings.MAGIC_LINK_MAX_PER_EMAIL = 1

    user_factory("one@example.com")
    user_factory("two@example.com")

    magic_link.request_link("one@example.com")
    magic_link.request_link("two@example.com")  # must not raise

    assert len(mail.outbox) == 2


def test_reset_clears_a_window(practitioner, settings):
    settings.MAGIC_LINK_MAX_PER_EMAIL = 1
    magic_link.request_link(practitioner.email)

    ratelimit.reset(magic_link.SCOPE_EMAIL, practitioner.email)

    magic_link.request_link(practitioner.email)  # must not raise
    assert len(mail.outbox) == 2


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


def test_purge_expired_removes_old_rows(practitioner):
    token, _ = magic_link.issue(practitioner)
    LoginToken.objects.filter(pk=token.pk).update(created_at=timezone.now() - timedelta(days=30))

    assert magic_link.purge_expired(older_than_days=7) == 1
    assert LoginToken.objects.count() == 0


def test_purge_expired_keeps_recent_rows(practitioner):
    magic_link.issue(practitioner)
    assert magic_link.purge_expired(older_than_days=7) == 0


def test_link_points_at_this_subdomain(practitioner, settings):
    """Canonicals and links stay inside this subdomain (docs/seo.md)."""
    settings.SITE_BASE_URL = "https://directory.kiamclinic.test"
    _, raw = magic_link.issue(practitioner)

    link = magic_link.build_link(raw)
    assert link.startswith("https://directory.kiamclinic.test/accounts/login/")
    assert "kiamclinic.com" not in link.replace("directory.kiamclinic.test", "")


def test_consume_url_reverses_with_the_token_in_the_path(practitioner):
    """Not a query string — keeps it out of Referer and out of analytics."""
    _, raw = magic_link.issue(practitioner)
    url = reverse("accounts:magic_link_consume", kwargs={"token": raw})
    assert url.endswith(f"/{raw}/")
    assert "?" not in url
