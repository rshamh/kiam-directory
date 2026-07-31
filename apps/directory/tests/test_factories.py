"""The factory traits behave as Phases 2–7 will assume.

Worth testing directly: every later phase's tests are written against these, so a
trait that silently stops doing its job takes a whole suite's meaning with it
while everything stays green.

The load-bearing one is that no factory writes a verification field. If
``verified=True`` ever set ``is_verified`` directly instead of creating evidence
and recomputing, every verification assertion downstream would be testing the
factory rather than the service.
"""

from __future__ import annotations

import pytest

from apps.directory.factories import PractitionerFactory, VerificationCheckFactory
from apps.directory.models import (
    DeliveryMode,
    MinorWorkStatus,
    PublicationStatus,
    VerificationStatus,
    VerificationType,
)

pytestmark = pytest.mark.django_db


def test_a_bare_practitioner_is_an_unverified_draft():
    practitioner = PractitionerFactory()

    assert practitioner.status == PublicationStatus.DRAFT
    assert practitioner.is_verified is False
    assert practitioner.minor_work_status == MinorWorkStatus.NOT_APPLICABLE
    assert practitioner.locations.count() == 0


def test_published_trait():
    practitioner = PractitionerFactory(published=True)

    assert practitioner.status == PublicationStatus.PUBLISHED
    assert practitioner.published_at is not None


def test_verified_trait_grants_the_badge_through_real_evidence():
    practitioner = PractitionerFactory(verified=True)

    assert practitioner.is_verified is True
    assert practitioner.credentials_checked_at is not None
    # The badge came from checks on file, not from a factory assignment.
    assert practitioner.verifications.filter(status=VerificationStatus.VERIFIED).exists()


def test_verified_trait_covers_a_prescriber_too():
    """required_types() grows for a prescriber; the trait has to follow."""
    practitioner = PractitionerFactory(prescriber=True, verified=True)

    assert practitioner.is_verified is True
    assert practitioner.verifications.filter(type=VerificationType.PRESCRIBER).exists()


def test_provisional_dbs_trait():
    practitioner = PractitionerFactory(provisional_dbs=True)

    assert practitioner.minor_work_status == MinorWorkStatus.PROVISIONAL
    assert practitioner.client_groups.filter(is_minors=True).exists()
    assert practitioner.can_show_minor_groups is False


def test_dbs_cleared_trait():
    practitioner = PractitionerFactory(dbs_cleared=True)

    assert practitioner.minor_work_status == MinorWorkStatus.CLEARED
    assert practitioner.can_show_minor_groups is True


def test_online_only_trait_has_no_location():
    """Online delivery is independent of location (load-bearing decision 2)."""
    practitioner = PractitionerFactory(online_only=True)

    assert practitioner.offers_online is True
    assert practitioner.delivery_mode == DeliveryMode.ONLINE
    assert practitioner.locations.count() == 0


def test_multi_location_trait_creates_two_distinct_places():
    """The Epsom-and-Guildford case (load-bearing decision 1)."""
    practitioner = PractitionerFactory(multi_location=True)

    cities = set(practitioner.locations.values_list("city", flat=True))
    assert cities == {"Epsom", "Guildford"}


def test_locations_count_is_settable():
    practitioner = PractitionerFactory(locations=1)
    assert practitioner.locations.count() == 1


def test_traits_compose():
    practitioner = PractitionerFactory(published=True, verified=True, multi_location=True)

    assert practitioner.status == PublicationStatus.PUBLISHED
    assert practitioner.is_verified is True
    assert practitioner.locations.count() == 2


def test_no_factory_writes_a_verification_field_directly():
    """The rule the whole computed-not-toggled design depends on.

    Deleting the evidence and recomputing must clear the badge. If a factory had
    set is_verified directly, this would still be True.
    """
    from apps.directory.services import verification

    practitioner = PractitionerFactory(verified=True)
    assert practitioner.is_verified is True

    practitioner.verifications.all().delete()
    verification.recompute(practitioner)

    assert practitioner.is_verified is False


def test_verification_check_factory_traits():
    practitioner = PractitionerFactory()

    expired = VerificationCheckFactory(
        practitioner=practitioner, type=VerificationType.INSURANCE, expired=True
    )
    assert expired.expires_at is not None

    rejected = VerificationCheckFactory(practitioner=practitioner, type=VerificationType.DBS, rejected=True)
    assert rejected.status == VerificationStatus.REJECTED


def test_factories_are_reusable_within_a_test():
    """django_get_or_create on slug means repeated taxonomy calls do not collide."""
    first = PractitionerFactory(dbs_cleared=True)
    second = PractitionerFactory(dbs_cleared=True)

    assert first.pk != second.pk
    assert first.minor_work_status == second.minor_work_status == MinorWorkStatus.CLEARED
