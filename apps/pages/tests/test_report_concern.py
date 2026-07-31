"""Report a concern about a listing.

The compliance requirement is a routing requirement: a complaint about someone's
*care* has to go to their regulator, because Kiam neither provided nor supervised
that care and cannot investigate it. So the tests here care as much about what
the page *says before the form* as about what the form writes.

The rest is the ordinary shape of a public form on this site: no account, no
CAPTCHA, honeypot plus a per-IP limit, and errors a person can act on.
"""

from __future__ import annotations

import pytest

from apps.directory.factories import PractitionerFactory
from apps.directory.models import ConcernReport, Practitioner, PublicationStatus

pytestmark = pytest.mark.django_db

URL = "/report-a-concern/"


@pytest.fixture
def published():
    return PractitionerFactory(published=True, slug="jane-example", full_name="Jane Example")


def _payload(**overrides):
    data = {
        "listing": "jane-example",
        "category": ConcernReport.Category.INACCURATE,
        "detail": "The listing gives a Guildford address, but that practice closed last year.",
        "reporter_email": "",
        "website": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# What the page says
# ---------------------------------------------------------------------------


def test_the_page_says_clinical_complaints_do_not_come_here(client):
    body = client.get(URL).content.decode()

    assert "cannot investigate the care they provided" in body
    assert "goes to the body\n  that regulates them" in body or "regulates them" in body


def test_the_page_names_the_regulators(client):
    body = client.get(URL).content.decode()

    for regulator in (
        "General Medical Council",
        "Health and Care Professions Council",
        "Nursing and Midwifery Council",
        "General Pharmaceutical Council",
        "Social Work England",
    ):
        assert regulator in body

    # And the professional bodies that hold the voluntary registers.
    for body_name in ("BACP", "UKCP", "BABCP", "BPS"):
        assert body_name in body


def test_the_routing_comes_before_the_form(client):
    """Somebody arriving angry fills in the first box they see."""
    body = client.get(URL).content.decode()

    assert body.index("cannot investigate the care") < body.index('name="detail"')


def test_the_page_signposts_urgent_help(client):
    body = client.get(URL).content.decode()
    assert "116 123" in body


def test_the_concern_page_does_not_fork_the_crisis_wording(client):
    """Crisis wording is Dr. Abbass's gate, and it exists once.

    This page carried its own copy differing by one word ("urgent help now"),
    which is exactly how gated copy drifts: two variants, one of them reviewed.
    It now renders the same resolved value the footer does.
    """
    from apps.pages import nav

    body = " ".join(client.get(URL).content.decode().split())

    assert nav.CRISIS_SIGNPOSTING in body
    # And the old variant is gone.
    assert "urgent help now" not in body


def test_the_listing_field_is_prefilled_from_a_profile_link(client, published):
    body = client.get(f"{URL}?listing={published.slug}").content.decode()

    assert 'value="jane-example"' in body


def test_the_profile_links_here_with_the_listing_named(client, published):
    body = client.get(f"/p/{published.slug}/").content.decode()

    assert f"/report-a-concern/?listing={published.slug}" in body


# ---------------------------------------------------------------------------
# Submitting
# ---------------------------------------------------------------------------


def test_a_valid_report_is_recorded(client, published):
    response = client.post(URL, _payload())

    assert response.status_code == 200
    report = ConcernReport.objects.get()
    assert report.practitioner_id == published.pk
    assert report.category == ConcernReport.Category.INACCURATE
    assert "Guildford" in report.detail
    assert report.handled_at is None


def test_a_successful_report_is_confirmed_and_announced(client, published):
    """The confirmation is the <h1> and the <title>, not a status box.

    This form does not swap — it is a plain POST — so a `role="status"` already
    in the DOM when the response arrives is announced by nothing: live regions
    only fire on mutation after registration. Re-rendering the same heading with
    a confirmation several screens down left a screen-reader user with no signal
    that anything had happened. A changed document title is what every screen
    reader announces on navigation.
    """
    body = client.post(URL, _payload()).content.decode()

    assert "<title>Thank you — your report has been received" in body
    assert "<h1" in body
    heading = body.split("<h1", 1)[1].split("</h1>", 1)[0]
    assert "Thank you — your report has been received" in heading

    # The form is gone, so nobody sends it twice by accident.
    assert 'name="detail"' not in body


def test_the_confirmation_keeps_the_regulator_routing_visible(client, published):
    """ "I've sent this and it should have gone to my regulator" is a likely
    realisation, and the answer should still be on the page when it lands."""
    body = client.post(URL, _payload()).content.decode()

    assert "General Medical Council" in body


def test_submitting_does_not_change_what_this_url_is(client, published):
    """The <title> carries the confirmation; the JSON-LD keeps the page's identity.

    What this URL *is* does not change because somebody posted to it.
    """
    import json

    body = client.post(URL, _payload()).content.decode()
    raw = body.split('<script type="application/ld+json">')[1].split("</script>")[0]

    assert json.loads(raw)["@graph"][0]["name"] == "Report a concern about a listing"


def test_a_report_can_be_anonymous(client, published):
    client.post(URL, _payload(reporter_email=""))

    assert ConcernReport.objects.get().reporter_email == ""


def test_an_email_address_is_kept_when_one_is_given(client, published):
    client.post(URL, _payload(reporter_email="worried@example.com"))

    assert ConcernReport.objects.get().reporter_email == "worried@example.com"


def test_a_report_stores_nothing_about_the_reporter_beyond_what_they_typed(client, published):
    """Minimal collection (golden rule #4). No IP, no user agent."""
    client.post(URL, _payload())

    from apps.directory.models import AuditLog

    entry = AuditLog.objects.get(action="concern.submitted")
    assert entry.ip is None
    assert entry.actor is None


def test_the_listing_can_be_identified_by_its_full_url(client, published):
    client.post(URL, _payload(listing="https://directory.kiamclinic.com/p/jane-example/"))

    assert ConcernReport.objects.get().practitioner_id == published.pk


def test_the_listing_can_be_identified_by_name(client, published):
    client.post(URL, _payload(listing="jane example"))

    assert ConcernReport.objects.get().practitioner_id == published.pk


def test_an_ambiguous_name_asks_for_the_link_instead_of_guessing(client, published):
    """Putting a concern on the wrong person's record is the failure to avoid."""
    PractitionerFactory(published=True, slug="jane-example-2", full_name="Jane Example")

    body = client.post(URL, _payload(listing="Jane Example")).content.decode()

    assert not ConcernReport.objects.exists()
    assert "We couldn&#x27;t find that listing" in body or "couldn" in body


def test_an_unknown_listing_is_a_plain_language_error(client):
    body = client.post(URL, _payload(listing="nobody-of-that-name")).content.decode()

    assert not ConcernReport.objects.exists()
    assert "directory.kiamclinic.com/p/" in body


def test_a_suspended_listing_answers_the_same_way_as_an_unknown_one(client, published):
    """This form must not become a way to confirm somebody was taken down."""
    Practitioner.objects.filter(pk=published.pk).update(status=PublicationStatus.SUSPENDED)

    suspended = client.post(URL, _payload()).content.decode()
    unknown = client.post(URL, _payload(listing="nobody-of-that-name")).content.decode()

    assert not ConcernReport.objects.exists()
    assert "Jane Example" not in suspended
    assert suspended.count("couldn") == unknown.count("couldn")


def test_the_category_must_be_chosen(client, published):
    body = client.post(URL, _payload(category="")).content.decode()

    assert not ConcernReport.objects.exists()
    assert "Please choose what the concern is about" in body


def test_the_detail_is_required(client, published):
    client.post(URL, _payload(detail=""))

    assert not ConcernReport.objects.exists()


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("listing", "Tell us which listing this is about"),
        ("category", "Please choose what the concern is about"),
        ("detail", "Tell us what you noticed"),
    ],
)
def test_every_required_field_says_what_is_missing(client, published, field, expected):
    """Not "This field is required."

    That names no field, offers no next step, and reads as a system message —
    the register the accessibility rules on this project rule out. Two of the
    three fields were still using Django's default.
    """
    body = client.post(URL, _payload(**{field: ""})).content.decode()

    assert expected in body
    assert "This field is required" not in body


def test_what_they_typed_survives_an_error(client, published):
    """Retyping a paragraph because of a typo in another field is a real cost."""
    body = client.post(URL, _payload(category="")).content.decode()

    assert "that practice closed last year" in body


# ---------------------------------------------------------------------------
# Spam protection, without a puzzle
# ---------------------------------------------------------------------------


def test_the_honeypot_writes_nothing_and_says_nothing(client, published):
    body = client.post(URL, _payload(website="https://buy-things.example")).content.decode()

    assert not ConcernReport.objects.exists()
    # Indistinguishable from success: telling a bot it was caught teaches it
    # which field to leave alone next time.
    assert "your report has been received" in body


def test_there_is_no_captcha(client):
    body = client.get(URL).content.decode().lower()

    assert "captcha" not in body
    assert "recaptcha" not in body


def test_reports_are_rate_limited_per_ip(client, published, settings):
    settings.CONCERN_REPORT_MAX_PER_IP = 2

    client.post(URL, _payload())
    client.post(URL, _payload())
    body = client.post(URL, _payload()).content.decode()

    assert ConcernReport.objects.count() == 2
    assert 'role="alert"' in body
    assert "several reports from your connection" in body


def test_an_invalid_submission_does_not_spend_the_limit(client, published, settings):
    """Somebody fixing a typo three times must not lock themselves out."""
    settings.CONCERN_REPORT_MAX_PER_IP = 1

    client.post(URL, _payload(listing="nobody-of-that-name"))
    client.post(URL, _payload(listing="also-nobody"))
    client.post(URL, _payload())

    assert ConcernReport.objects.count() == 1


# ---------------------------------------------------------------------------
# It reaches the queue staff actually work
# ---------------------------------------------------------------------------


def test_a_submitted_report_appears_in_the_back_office_queue(client, published):
    from apps.backoffice.services import concerns

    client.post(URL, _payload(category=ConcernReport.Category.NOT_REGISTERED))

    queue = list(concerns.open_queue())
    assert len(queue) == 1
    assert queue[0].practitioner_id == published.pk
