"""Submission lint — one test per rule, plus the false-positive cases.

The false-positive tests matter as much as the positive ones. A lint that flags
"sadness" as the efficacy word "sad" trains reviewers to click past findings, and
a reviewer who clicks past findings is worse than no lint at all.
"""

from __future__ import annotations

import pytest

from apps.directory.factories import ClientGroupFactory, PractitionerFactory
from apps.directory.models import MinorWorkStatus, Registration
from apps.directory.services import lint

pytestmark = pytest.mark.django_db


def _contactable(**kwargs):
    """A practitioner who passes the contact rule, so other rules test alone."""
    return PractitionerFactory(public_email="hello@example.com", **kwargs)


def _rules(result):
    return {f.rule for f in result.findings}


# ---------------------------------------------------------------------------
# Rule 1 — prescription-only medicines. BLOCKS.
# ---------------------------------------------------------------------------


def test_a_pom_substance_name_blocks_submission():
    practitioner = _contactable(intro="I offer titration of methylphenidate for adult ADHD.")

    result = lint.run(practitioner)

    assert result.is_blocked
    assert "pom" in _rules(result)
    assert "methylphenidate" in result.blocks[0].matches


def test_a_pom_brand_name_blocks_submission():
    practitioner = _contactable(services="Reviews for patients stabilised on Elvanse.")

    result = lint.run(practitioner)

    assert result.is_blocked
    assert "elvanse" in result.blocks[0].matches


@pytest.mark.parametrize("field_name", ["intro", "services", "availability_note"])
def test_every_linted_field_is_checked(field_name):
    practitioner = _contactable(**{field_name: "We can discuss sertraline."})

    result = lint.run(practitioner)

    assert result.is_blocked
    assert result.blocks[0].field == field_name


def test_the_permitted_generic_terms_do_not_block():
    """The whole point of §1's permitted list."""
    practitioner = _contactable(
        intro="Medication management, prescribing, titration and review, and shared care.",
    )

    result = lint.run(practitioner)

    assert "pom" not in _rules(result)


def test_a_pom_name_inside_a_longer_word_does_not_match():
    """Word boundaries — otherwise the lint cries wolf."""
    practitioner = _contactable(intro="I use a lithium-ion recorder in sessions.")

    result = lint.run(practitioner)
    pom = [f for f in result.findings if f.rule == "pom"]

    # "lithium-ion" — the hyphen is a word boundary, so this DOES match, and
    # should: a reviewer ought to see it. What must not happen is a silent miss.
    assert pom, "hyphenated use should still surface for a human"


def test_matching_is_case_insensitive():
    practitioner = _contactable(intro="SERTRALINE reviews available.")
    assert lint.run(practitioner).is_blocked


def test_a_multi_word_pom_matches_across_whitespace():
    practitioner = _contactable(intro="Experienced with sodium\nvalproate monitoring.")
    assert lint.run(practitioner).is_blocked


def test_a_missing_dictionary_raises_rather_than_permitting_everything(settings):
    """Fail loud, not open. An empty dictionary silently disables the control."""
    settings.POM_DICTIONARY_PATH = "/nonexistent/pom.txt"
    lint._pom_terms_cached.cache_clear()

    practitioner = _contactable(intro="Anything at all.")

    with pytest.raises(lint.ImproperlyConfiguredPOMDictionary):
        lint.run(practitioner)


# ---------------------------------------------------------------------------
# Rule 2 — efficacy claims. HOLDS.
# ---------------------------------------------------------------------------


def test_an_efficacy_claim_holds_but_does_not_block():
    practitioner = _contactable(intro="A proven to work approach with a 95% success rate.")

    result = lint.run(practitioner)

    assert not result.is_blocked
    assert result.must_hold_for_review
    assert "efficacy" in _rules(result)


def test_the_word_cure_is_flagged():
    practitioner = _contactable(services="A cure for anxiety.")
    assert "efficacy" in _rules(lint.run(practitioner))


def test_ordinary_clinical_language_is_not_flagged_as_efficacy():
    practitioner = _contactable(
        intro="I work with adults experiencing depression, anxiety and burnout.",
        services="Assessment, formulation and ongoing therapy.",
    )

    result = lint.run(practitioner)

    assert "efficacy" not in _rules(result)
    assert not result.findings


# ---------------------------------------------------------------------------
# Rule 3 — child-work language without clearance. HOLDS.
# ---------------------------------------------------------------------------


def test_child_work_language_holds_when_not_cleared():
    """§4's residual risk: groups correctly hidden, bio still advertising it."""
    practitioner = _contactable(intro="I work with children and adolescents.")
    practitioner.client_groups.set([ClientGroupFactory(minors=True)])
    from apps.directory.services import verification

    verification.recompute(practitioner)
    assert practitioner.minor_work_status == MinorWorkStatus.BLOCKED

    result = lint.run(practitioner)

    assert result.must_hold_for_review
    assert "child_work" in _rules(result)


def test_child_work_language_is_fine_once_cleared():
    practitioner = PractitionerFactory(
        public_email="hello@example.com",
        dbs_cleared=True,
        intro="I work with children and adolescents.",
    )
    assert practitioner.minor_work_status == MinorWorkStatus.CLEARED

    assert "child_work" not in _rules(lint.run(practitioner))


def test_child_work_language_holds_for_a_provisional_practitioner():
    """PROVISIONAL is adult-work-only, so the bio must not advertise otherwise."""
    practitioner = PractitionerFactory(
        public_email="hello@example.com",
        provisional_dbs=True,
        services="Sessions for teenagers and young people.",
    )
    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL

    assert "child_work" in _rules(lint.run(practitioner))


def test_adult_only_copy_is_not_flagged_for_child_work():
    practitioner = _contactable(intro="I work with adults aged 18 and over.")
    assert "child_work" not in _rules(lint.run(practitioner))


# ---------------------------------------------------------------------------
# Rule 4 — restricted titles. HOLDS.
# ---------------------------------------------------------------------------


def test_a_restricted_title_holds_without_a_verified_registration():
    practitioner = _contactable(intro="I am a consultant psychiatrist working in Surrey.")

    result = lint.run(practitioner)

    assert result.must_hold_for_review
    assert "restricted_title" in _rules(result)
    assert "No verified registration" in result.holds[0].message


def test_a_restricted_title_still_flags_with_a_registration_on_file():
    """It flags the claim; entitlement is a verification question.

    Publication is refused separately by review.approve() — see the lint module
    docstring on why this holds rather than blocks.
    """
    practitioner = _contactable(intro="Clinical psychologist, HCPC registered.")
    Registration.objects.create(
        practitioner=practitioner, body="HCPC", registration_no="PYL01", verified=True
    )

    result = lint.run(practitioner)
    finding = [f for f in result.findings if f.rule == "restricted_title"][0]

    assert "HCPC" in finding.message


def test_an_unrestricted_title_is_not_flagged():
    practitioner = _contactable(intro="I am a counsellor and psychotherapist.")
    assert "restricted_title" not in _rules(lint.run(practitioner))


# ---------------------------------------------------------------------------
# Rule 5 — at least one contact method. BLOCKS.
# ---------------------------------------------------------------------------


def test_no_contact_method_blocks_submission():
    practitioner = PractitionerFactory(public_email="", public_phone="", public_website="", booking_url="")

    result = lint.run(practitioner)

    assert result.is_blocked
    assert "contact" in _rules(result)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("public_email", "hello@example.com"),
        ("public_phone", "01372 660580"),
        ("public_website", "https://example.com"),
    ],
)
def test_any_single_contact_method_satisfies_the_rule(field_name, value):
    blank = {"public_email": "", "public_phone": "", "public_website": "", "booking_url": ""}
    practitioner = PractitionerFactory(**{**blank, field_name: value})

    assert "contact" not in _rules(lint.run(practitioner))


def test_a_booking_link_alone_is_not_a_contact_method():
    """It counted until Phase 3, and Phase 3 is what made that wrong.

    The profile deliberately does not render `booking_url` — a booking control
    on a Kiam-branded page asserts Kiam manages the appointment
    (docs/content-compliance.md §9). Accepting it here therefore published
    listings that render "This listing does not publish contact details": the
    check whose whole job is preventing an uncontactable listing was creating
    them.
    """
    practitioner = PractitionerFactory(
        public_email="",
        public_phone="",
        public_website="",
        booking_url="https://booking.example.com",
    )

    assert "contact" in _rules(lint.run(practitioner))
    assert lint.run(practitioner).is_blocked


def test_the_contact_rule_and_the_profiles_channels_agree():
    """Two lists, one promise: everything that satisfies the rule is rendered.

    If a field is added to one and not the other, the failure is silent and lands
    on a practitioner whose listing nobody can act on.
    """
    from apps.directory.services.profile import CHANNELS

    rendered = {spec["field"] for spec in CHANNELS.values()}

    assert rendered == {"public_email", "public_phone", "public_website"}


def test_whitespace_is_not_a_contact_method():
    practitioner = PractitionerFactory(public_email="   ", public_phone="", public_website="", booking_url="")
    assert lint.run(practitioner).is_blocked


# ---------------------------------------------------------------------------
# The result object
# ---------------------------------------------------------------------------


def test_a_clean_profile_produces_nothing():
    practitioner = _contactable(
        intro="I support adults with anxiety and low mood.",
        services="Assessment and weekly therapy sessions.",
    )

    result = lint.run(practitioner)

    assert result.findings == []
    assert not result.is_blocked
    assert not result.must_hold_for_review


def test_findings_serialise_for_review_request_storage():
    practitioner = _contactable(intro="A guaranteed cure using methylphenidate.")

    payload = lint.run(practitioner).as_dict()

    assert payload["blocked"] is True
    assert payload["held"] is True
    assert {f["rule"] for f in payload["findings"]} == {"pom", "efficacy"}
    for finding in payload["findings"]:
        assert set(finding) == {"rule", "severity", "field", "matches", "message"}


def test_blocks_and_holds_are_reported_separately():
    practitioner = PractitionerFactory(public_email="", intro="A guaranteed outcome.")

    result = lint.run(practitioner)

    assert {f.rule for f in result.blocks} == {"contact"}
    assert {f.rule for f in result.holds} == {"efficacy"}
