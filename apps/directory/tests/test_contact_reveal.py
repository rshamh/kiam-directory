"""The contact reveal.

Four things have to hold at once, and each of them is a different kind of
failure if it does not:

* the details are **not in the profile HTML** — otherwise the reveal is theatre;
* the reveal **works without JavaScript** — otherwise the listing is unreachable
  for a chunk of the audience;
* every reveal **restates the independence relationship** verbatim
  (docs/content-compliance.md §5) and **counts** (the metric is the practitioner's
  evidence that the listing does something);
* the endpoint is **not a scraping API** — POST only, rate limited, noindex.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.directory.factories import PractitionerFactory
from apps.directory.models import DailyMetric, Practitioner, PublicationStatus

pytestmark = pytest.mark.django_db

NOTICE = (
    "Practitioners listed in the Kiam Clinic Directory are independent professionals. "
    "They are not employed by, or part of the clinical team at, Kiam Clinic. Clients "
    "arrange appointments and payment directly with the practitioner."
)


@pytest.fixture
def published():
    return PractitionerFactory(
        published=True,
        slug="contactable-example",
        full_name="Jane Example",
        display_title="Dr",
        public_email="jane@example.com",
        public_phone="07700 900123",
        public_website="https://jane.example.com",
    )


def _url(practitioner, channel):
    return reverse("directory:contact_reveal", args=[practitioner.slug, channel])


def _htmx(client, practitioner, channel):
    return client.post(_url(practitioner, channel), HTTP_HX_REQUEST="true")


# ---------------------------------------------------------------------------
# Nothing is in the page until it is asked for
# ---------------------------------------------------------------------------


def test_the_profile_page_contains_no_contact_details(client, published):
    body = client.get(f"/p/{published.slug}/").content.decode()

    assert "jane@example.com" not in body
    assert "07700 900123" not in body
    assert "jane.example.com" not in body

    # mailto:/tel: are scoped to <main>: the kiam-ui header carries the CLINIC's
    # own address and number on every page, which is Kiam contacting itself and
    # not a practitioner's details leaking.
    main = body.split("<main", 1)[1].split("</main>", 1)[0]
    assert "mailto:" not in main
    assert "tel:" not in main


def test_the_profile_page_offers_a_button_for_each_published_channel(client, published):
    body = client.get(f"/p/{published.slug}/").content.decode()

    assert "Show email address" in body
    assert "Show phone number" in body
    assert "Show website address" in body


def test_a_channel_the_practitioner_left_blank_is_not_offered(client):
    practitioner = PractitionerFactory(published=True, slug="email-only", public_email="only@example.com")

    body = client.get(f"/p/{practitioner.slug}/").content.decode()

    assert "Show email address" in body
    assert "Show phone number" not in body


# ---------------------------------------------------------------------------
# Revealing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "value"),
    [
        ("email", "jane@example.com"),
        ("phone", "07700 900123"),
        ("website", "https://jane.example.com"),
    ],
)
def test_each_channel_reveals_its_detail(client, published, channel, value):
    response = _htmx(client, published, channel)

    assert response.status_code == 200
    assert value in response.content.decode()


def test_the_reveal_restates_the_independence_relationship_verbatim(client, published):
    """docs/content-compliance.md §5 — and above the detail, not beside it."""
    body = " ".join(_htmx(client, published, "email").content.decode().split())

    assert NOTICE in body
    assert body.index("independent professionals") < body.index("jane@example.com")


def test_the_reveal_is_a_focus_destination_with_a_name(client, published):
    """Focus moves here after the swap, so it needs a role and an accessible name."""
    body = _htmx(client, published, "email").content.decode()

    assert 'tabindex="-1"' in body
    assert "data-reveal-focus" in body
    assert 'role="group"' in body
    assert 'id="reveal-email-heading"' in body


def test_the_focus_announcement_carries_the_revealed_detail(client, published):
    """Focusing a group announces its NAME, not its contents.

    A screen reader parks the cursor and reads the accessible name and role; it
    does not read the descendants until the user arrows down. Labelling the panel
    by its heading alone would announce "Email address, group" and leave the
    address unread — so the label spans the heading AND the value, and the focus
    move itself says "Email address, jane@example.com".
    """
    body = _htmx(client, published, "email").content.decode()

    assert 'aria-labelledby="reveal-email-heading reveal-email-value"' in body
    assert 'id="reveal-email-value"' in body


def test_the_email_and_phone_are_actionable_links(client, published):
    assert "mailto:jane@example.com" in _htmx(client, published, "email").content.decode()
    # Spaces on screen, none in the href — a dialler cannot parse "07700 900123".
    assert 'href="tel:07700900123"' in _htmx(client, published, "phone").content.decode()


def test_the_website_link_does_not_pass_authority(client, published):
    """Listings are a paid membership; a followed link from one is a link scheme.

    `sponsored` is what Google's current guidance asks for; `nofollow` stays for
    crawlers that predate it.
    """
    body = _htmx(client, published, "website").content.decode()
    assert 'rel="sponsored nofollow noopener"' in body


def test_a_regulator_register_link_is_followed(client, published):
    """The opposite case, and the distinction is the point.

    A link to the GMC or HCPC public register is the evidence behind the claim
    the page is making — an authoritative citation, not paid placement. Blanket
    `nofollow` on every outbound link would withhold the one that supports the
    verification badge.
    """
    from apps.directory.models import Registration

    Registration.objects.create(
        practitioner=published,
        body="GMC",
        registration_no="1234567",
        verified=True,
        register_url="https://www.gmc-uk.org/example",
    )

    body = client.get(f"/p/{published.slug}/").content.decode()
    register_link = body.split('href="https://www.gmc-uk.org/example"')[1].split(">")[0]

    assert "nofollow" not in register_link
    assert "noopener" in register_link


# ---------------------------------------------------------------------------
# Without JavaScript
# ---------------------------------------------------------------------------


def test_a_plain_form_post_returns_a_whole_page(client, published):
    response = client.post(_url(published, "email"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "<!doctype html>" in body.lower()
    assert "jane@example.com" in body
    assert NOTICE in " ".join(body.split())
    # And a way back.
    assert f"/p/{published.slug}/" in body


def test_the_htmx_response_is_a_fragment_not_a_page(client, published):
    body = _htmx(client, published, "email").content.decode()
    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()


def test_the_standalone_reveal_page_is_noindex(client, published):
    response = client.post(_url(published, "email"))

    assert 'content="noindex, nofollow"' in response.content.decode()
    assert response["X-Robots-Tag"] == "noindex, nofollow"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("channel", "field"),
    [("email", "email_reveals"), ("phone", "phone_reveals"), ("website", "website_clicks")],
)
def test_a_reveal_increments_the_right_counter(client, published, channel, field):
    _htmx(client, published, channel)

    metric = DailyMetric.objects.get(practitioner=published)
    assert getattr(metric, field) == 1
    # And nothing else moved.
    others = {"email_reveals", "phone_reveals", "website_clicks"} - {field}
    assert all(getattr(metric, other) == 0 for other in others)


def test_two_reveals_count_twice(client, published):
    _htmx(client, published, "email")
    _htmx(client, published, "email")

    assert DailyMetric.objects.get(practitioner=published).email_reveals == 2


# ---------------------------------------------------------------------------
# Not a scraping API
# ---------------------------------------------------------------------------


def test_the_reveal_endpoint_refuses_get(client, published):
    """A GET URL that returns an email address is a URL a crawler will follow."""
    assert client.get(_url(published, "email")).status_code == 405


def test_an_unknown_channel_has_no_url_at_all(client, published):
    assert client.post(f"/p/{published.slug}/contact/postal/").status_code == 404


def test_a_channel_with_no_value_is_a_404(client):
    practitioner = PractitionerFactory(published=True, slug="no-phone", public_email="a@example.com")

    assert client.post(_url(practitioner, "phone")).status_code == 404


def test_an_unpublished_listing_reveals_nothing(client, published):
    Practitioner.objects.filter(pk=published.pk).update(status=PublicationStatus.SUSPENDED)

    assert client.post(_url(published, "email")).status_code == 404


def test_an_old_slug_cannot_be_posted_to(client, published):
    """A redirect exists for reading a profile, not for harvesting from one."""
    Practitioner.objects.filter(pk=published.pk).update(slug="contactable-example")
    published.slug = "renamed-example"
    published.save()

    assert client.post(_url(published, "email")).status_code == 200
    assert client.post("/p/contactable-example/contact/email/").status_code == 404


def test_the_reveal_is_rate_limited_per_ip(client, published, settings):
    settings.CONTACT_REVEAL_MAX_PER_IP = 2

    assert _htmx(client, published, "email").status_code == 200
    assert _htmx(client, published, "email").status_code == 200

    refused = _htmx(client, published, "email")
    assert refused.status_code == 429
    assert "jane@example.com" not in refused.content.decode()


def test_a_refused_reveal_counts_nothing(client, published, settings):
    settings.CONTACT_REVEAL_MAX_PER_IP = 1

    _htmx(client, published, "email")
    _htmx(client, published, "email")

    assert DailyMetric.objects.get(practitioner=published).email_reveals == 1


def test_a_refusal_is_announced_and_blames_nobody(client, published, settings):
    settings.CONTACT_REVEAL_MAX_PER_IP = 0

    body = _htmx(client, published, "email").content.decode()

    # The same focus pattern as a successful reveal, not `role="alert"` on top of
    # it — alert plus a focus move is the double announcement the success path
    # deliberately avoids, and a refusal is no reason to shout twice.
    assert 'role="group"' in body
    assert "data-reveal-focus" in body
    assert 'aria-labelledby="reveal-limited-heading"' in body

    # The heading is what the focus move announces, so it has to say what happened.
    assert "Contact details are paused for now" in body
    assert "try again later" in body
    assert "nothing has been recorded against you" in body


def test_the_refusal_is_actually_swapped_into_the_page(client, published):
    """htmx 2 does not swap 4xx responses by default.

    Its stock `responseHandling` is `[{code:"[45]..", swap:false, error:true}]`,
    so the 429 partial was being fetched, discarded, and never shown: the button
    sat unchanged and the click did nothing — the exact silence the refusal copy
    exists to prevent. The slot opts 429 in via `hx-on::before-swap`.

    Asserting the server emitted `role="alert"` proved nothing about the browser,
    which is how this survived the first pass.
    """
    body = client.get(f"/p/{published.slug}/").content.decode()

    assert "hx-on::before-swap" in body
    assert "429" in body
    assert "shouldSwap = true" in body


def test_the_refusal_keeps_the_independence_notice(client, published, settings):
    """No detail is disclosed, so this is not the §5 interstitial — but it is a
    public page headed with a practitioner's name that sends the reader off to
    find them another way, and the relationship should not lapse because the
    answer was "not now"."""
    settings.CONTACT_REVEAL_MAX_PER_IP = 0

    body = " ".join(_htmx(client, published, "email").content.decode().split())

    assert NOTICE in body
