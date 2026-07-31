"""Test factories.

Phases 2–7 build on these, so the traits are the vocabulary the rest of the suite
will speak: ``published``, ``verified``, ``provisional_dbs``, ``online_only``,
``multi_location``.

Two things they deliberately do NOT do:

* **No factory writes a verification field.** ``verified`` and ``provisional_dbs``
  create dated ``VerificationCheck`` rows and then call
  ``verification.recompute()``, exactly as production does. A factory that set
  ``is_verified=True`` directly would let a test pass against a badge that the
  real code would never grant, which is precisely the bug the whole
  computed-not-toggled rule exists to prevent.
* **No factory guesses at taxonomy.** The vocabulary comes from
  ``seed_taxonomy``; these factories create the minimum terms a test needs, keyed
  on slug so they compose with a seeded database rather than duplicating it.
* **No factory writes ``completeness`` either**, for the same reason and since
  Phase 6, which gave that column a single writer and a ``post_save`` signal.
  ``PractitionerFactory(completeness=90)`` would be silently overwritten on save;
  ``PractitionerFactory(complete=True)`` fills in the intro, the photo, the
  contact route, the taxonomy and the credentials that make the score 100.
"""

from __future__ import annotations

from datetime import timedelta

import factory
from django.contrib.gis.geos import Point
from django.utils import timezone
from factory.django import DjangoModelFactory

from . import models

# Epsom, roughly — the clinic's own postcode district, so distances in search
# tests are recognisable rather than arbitrary.
EPSOM = (-0.2674, 51.3360)
GUILDFORD = (-0.5704, 51.2362)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


class ProfessionFactory(DjangoModelFactory):
    class Meta:
        model = models.Profession
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"profession-{n}")
    name = factory.Sequence(lambda n: f"Profession {n}")
    restricted = False
    required_bodies = factory.List([])

    class Params:
        # A restricted title cannot publish without a verified registration from
        # one of required_bodies (docs/content-compliance.md §3).
        restricted_title = factory.Trait(
            restricted=True,
            slug="consultant-psychiatrist",
            name="Consultant Psychiatrist",
            required_bodies=factory.List(["GMC"]),
        )


class SpecialityCategoryFactory(DjangoModelFactory):
    class Meta:
        model = models.SpecialityCategory
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"category-{n}")
    name = factory.Sequence(lambda n: f"Category {n}")


class SpecialityFactory(DjangoModelFactory):
    class Meta:
        model = models.Speciality
        django_get_or_create = ("slug",)

    category = factory.SubFactory(SpecialityCategoryFactory)
    slug = factory.Sequence(lambda n: f"speciality-{n}")
    name = factory.Sequence(lambda n: f"Speciality {n}")
    synonyms = factory.List([])
    implies_minors = False


class ApproachFactory(DjangoModelFactory):
    class Meta:
        model = models.Approach
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"approach-{n}")
    name = factory.Sequence(lambda n: f"Approach {n}")


class ClientGroupFactory(DjangoModelFactory):
    class Meta:
        model = models.ClientGroup
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"client-group-{n}")
    name = factory.Sequence(lambda n: f"Client group {n}")
    is_minors = False

    class Params:
        # The one that matters: is_minors drives the enhanced-DBS requirement and
        # the under-18 search gate.
        minors = factory.Trait(
            slug="adolescents", name="Adolescents (12–17)", min_age=12, max_age=17, is_minors=True
        )


class LanguageFactory(DjangoModelFactory):
    class Meta:
        model = models.Language
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"l{n}")
    name = factory.Sequence(lambda n: f"Language {n}")


class FundingOptionFactory(DjangoModelFactory):
    class Meta:
        model = models.FundingOption
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"funding-{n}")
    name = factory.Sequence(lambda n: f"Funding {n}")
    group = models.FundingOption.Group.SELF_PAY


class SessionFormatFactory(DjangoModelFactory):
    class Meta:
        model = models.SessionFormat
        django_get_or_create = ("slug",)

    slug = factory.Sequence(lambda n: f"format-{n}")
    name = factory.Sequence(lambda n: f"Format {n}")


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------


class PractitionerLocationFactory(DjangoModelFactory):
    """A practice address.

    Never set on the Practitioner itself: a practitioner has MANY locations and
    search matches ANY of them (docs/architecture.md, load-bearing decision 1).
    """

    class Meta:
        model = models.PractitionerLocation

    practitioner = factory.SubFactory("apps.directory.factories.PractitionerFactory")
    city = "Epsom"
    postcode = "KT18 5EP"
    geo = factory.LazyFunction(lambda: Point(*EPSOM, srid=4326))
    is_primary = True

    class Params:
        guildford = factory.Trait(
            city="Guildford",
            postcode="GU1 3UY",
            is_primary=False,
            geo=factory.LazyFunction(lambda: Point(*GUILDFORD, srid=4326)),
        )
        # Address used for the search radius but not displayed.
        private_address = factory.Trait(is_public=False)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


class VerificationCheckFactory(DjangoModelFactory):
    class Meta:
        model = models.VerificationCheck
        django_get_or_create = ("practitioner", "type")

    practitioner = factory.SubFactory("apps.directory.factories.PractitionerFactory")
    type = models.VerificationType.IDENTITY
    status = models.VerificationStatus.VERIFIED
    checked_at = factory.LazyFunction(timezone.now)

    class Params:
        expired = factory.Trait(
            status=models.VerificationStatus.VERIFIED,
            expires_at=factory.LazyFunction(lambda: timezone.now() - timedelta(days=1)),
        )
        pending = factory.Trait(status=models.VerificationStatus.IN_REVIEW, checked_at=None)
        rejected = factory.Trait(status=models.VerificationStatus.REJECTED, checked_at=None)


# ---------------------------------------------------------------------------
# Practitioner
# ---------------------------------------------------------------------------


class PractitionerFactory(DjangoModelFactory):
    class Meta:
        model = models.Practitioner
        skip_postgeneration_save = True

    slug = factory.Sequence(lambda n: f"practitioner-{n}")
    full_name = factory.Sequence(lambda n: f"Dr Test Practitioner {n}")
    profession = factory.SubFactory(ProfessionFactory)
    intro = "An independent practitioner offering assessment and ongoing support."
    services = "Assessment, review, and ongoing support."
    offers_online = True
    accepting_new_clients = True

    # -- M2M -----------------------------------------------------------------

    @factory.post_generation
    def specialities(self, create, extracted, **kwargs):
        if create and extracted:
            self.specialities.set(extracted)

    @factory.post_generation
    def approaches(self, create, extracted, **kwargs):
        if create and extracted:
            self.approaches.set(extracted)

    @factory.post_generation
    def client_groups(self, create, extracted, **kwargs):
        if create and extracted:
            self.client_groups.set(extracted)

    @factory.post_generation
    def languages(self, create, extracted, **kwargs):
        if create and extracted:
            self.languages.set(extracted)

    # -- side-effecting traits ----------------------------------------------
    #
    # These are post_generation declarations, NOT `factory.Trait`, and that is
    # forced rather than stylistic: a Trait sets values in the "attributes" phase,
    # so pointing one at a post_generation declaration either raises
    # ("Inconsistent phases") or — worse, with a plain value — silently routes
    # past the hook and never runs it. The call syntax is identical either way:
    # PractitionerFactory(verified=True) works the same.

    @factory.post_generation
    def locations(self, create, extracted, **kwargs):
        """`extracted` is a count of practice addresses."""
        if not create or not extracted:
            return
        PractitionerLocationFactory(practitioner=self)
        for _ in range(int(extracted) - 1):
            PractitionerLocationFactory(practitioner=self, guildford=True)

    @factory.post_generation
    def multi_location(self, create, extracted, **kwargs):
        """A therapist working Tuesdays in Epsom and Thursdays in Guildford.

        The case that makes PractitionerLocation a FK collection rather than
        fields on Practitioner — they must surface in BOTH searches.
        """
        if not create or not extracted:
            return
        if not self.locations.exists():
            PractitionerLocationFactory(practitioner=self)
        PractitionerLocationFactory(practitioner=self, guildford=True)

    @factory.post_generation
    def complete(self, create, extracted, **kwargs):
        """Fill in everything `directory.services.completeness` scores.

        Phase 6 made `completeness` a computed column with one writer and a
        `post_save` signal, so `PractitionerFactory(completeness=90)` is now
        overwritten the moment the row is saved — which is correct, and is exactly
        the same rule the verification traits already follow: **a factory makes the
        data that produces the derived value, never the derived value.** A test
        that set the number directly was asserting against a listing production
        would score at 10.

        This trait produces a listing that genuinely scores 100, which is what a
        test needing a home-page-grid-eligible practitioner actually means.
        """
        if not create or not extracted:
            return

        from apps.directory.services import completeness

        self.intro = (
            "I am an independent practitioner working with adults across a range of "
            "difficulties, including anxiety, low mood and the after-effects of "
            "difficult experiences. My work is collaborative: we start by getting a "
            "clear picture of what is going on for you, agree what would be most "
            "useful, and go at a pace that suits you. I have worked in both NHS and "
            "private settings and I see people online and in person."
        )
        self.services = (
            "Initial assessment, individual therapy, and review appointments. "
            "Sessions run for fifty minutes, usually weekly to begin with."
        )
        self.public_email = self.public_email or f"{self.slug}@example.com"
        self.online_coverage = self.online_coverage or "UK-wide"
        self.fee_min = self.fee_min or 9000
        self.typical_wait = self.typical_wait or models.WaitTime.SHORT
        self.headshot = self.headshot or "headshots/test.jpg"
        self.save()

        if not self.specialities.exists():
            self.specialities.add(SpecialityFactory())
        if not self.approaches.exists():
            self.approaches.add(ApproachFactory())
        if not self.client_groups.exists():
            self.client_groups.add(ClientGroupFactory())
        if not self.languages.exists():
            self.languages.add(LanguageFactory())

        if not self.qualifications.exists():
            models.Qualification.objects.create(
                practitioner=self, title="MSc Psychology", institution="A university", year=2015
            )
        if not self.registrations.exists():
            models.Registration.objects.create(practitioner=self, body="BACP", registration_no="123456")
        # Deliberately NO location. `online_coverage` already satisfies the
        # "where you work is clear" requirement, so adding one would be a side
        # effect this trait does not need — and it would put an Epsom address on
        # every complete practitioner, quietly dominating any test that groups by
        # town. A trait should do the minimum that makes its claim true.

        completeness.recompute(self)

    @factory.post_generation
    def verified(self, create, extracted, **kwargs):
        if create and extracted:
            _grant_required_evidence(self)
            _recompute(self)

    @factory.post_generation
    def provisional_dbs(self, create, extracted, **kwargs):
        """Live for ADULT WORK ONLY while an enhanced DBS is pending."""
        if not create or not extracted:
            return
        _ensure_minor_group(self)
        _grant_required_evidence(self)
        _open_provisional_window(self)
        _recompute(self)

    @factory.post_generation
    def dbs_cleared(self, create, extracted, **kwargs):
        if not create or not extracted:
            return
        _ensure_minor_group(self)
        _grant_required_evidence(self)
        _grant_dbs(self)
        _recompute(self)

    class Params:
        # Field-only traits are safe as Traits — nothing here needs the database
        # after the instance exists.
        published = factory.Trait(
            status=models.PublicationStatus.PUBLISHED,
            published_at=factory.LazyFunction(timezone.now),
            last_active_at=factory.LazyFunction(timezone.now),
        )
        # Online delivery is independent of location — a complete listing with no
        # physical address at all (load-bearing decision 2).
        online_only = factory.Trait(
            offers_online=True,
            delivery_mode=models.DeliveryMode.ONLINE,
            online_coverage="UK-wide",
        )
        in_person_only = factory.Trait(
            offers_online=False,
            delivery_mode=models.DeliveryMode.IN_PERSON,
        )
        prescriber = factory.Trait(is_prescriber=True)


# ---------------------------------------------------------------------------
# Evidence helpers
#
# Kept as functions rather than inlined so every trait grants evidence the same
# way, and so the state always arrives via verification.recompute() rather than
# being written directly.
# ---------------------------------------------------------------------------


def _recompute(practitioner):
    from .services import verification

    verification.recompute(practitioner)


def _ensure_minor_group(practitioner):
    if not practitioner.client_groups.filter(is_minors=True).exists():
        practitioner.client_groups.add(ClientGroupFactory(minors=True))


def _grant_required_evidence(practitioner):
    """Every check the profile requires, verified and in date."""
    from .services import verification

    now = timezone.now()
    for check_type in verification.required_types(practitioner):
        VerificationCheckFactory(
            practitioner=practitioner,
            type=check_type,
            status=models.VerificationStatus.VERIFIED,
            # Backdated so `credentials_checked_at` has an oldest to find and a
            # test can tell "oldest" from "newest".
            checked_at=now - timedelta(days=30),
            expires_at=(
                now + timedelta(days=365) if check_type == models.VerificationType.INSURANCE else None
            ),
        )


def _open_provisional_window(practitioner):
    from .services import verification

    now = timezone.now()
    VerificationCheckFactory(
        practitioner=practitioner,
        type=models.VerificationType.DBS,
        status=models.VerificationStatus.IN_REVIEW,
        checked_at=None,
        provisional_granted_at=now,
        provisional_until=now + timedelta(days=verification.PROVISIONAL_WINDOW_DAYS),
    )


def _grant_dbs(practitioner):
    now = timezone.now()
    VerificationCheckFactory(
        practitioner=practitioner,
        type=models.VerificationType.DBS,
        status=models.VerificationStatus.VERIFIED,
        checked_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=3 * 365),
    )
