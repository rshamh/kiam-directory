"""Fill a development database with practitioners you can actually search.

    python manage.py seed_demo --fresh

**It refuses to run unless DEBUG is on.** Every practitioner it creates is
fictional, and several of them carry a "Credentials checked" badge — which on this
site is a claim Kiam makes about a real person's real documents. Fake verified
clinicians in a database that is reachable from the internet is a worse problem
than having no demo data, so the guard is not a convenience check and
``--force`` is deliberately not offered.

Two rules it follows because production follows them:

* **No verification field is ever written directly.** The badge comes from
  ``verification.verify_all_required()`` and the DBS states from
  ``grant_provisional_dbs()`` / ``set_check_status()`` — the same functions the
  workbench calls. That means running this also exercises those paths, and a
  practitioner's badge here is as real as the evidence rows behind it.
* **Publication goes through the state machine** where it matters. These land
  directly in PUBLISHED because the point is to have something to search; the
  invite-to-publish flow has its own tests.

The dataset is chosen to make the facets do something rather than to look tidy:
every DBS state, every delivery mode, lapsed as well as current badges, three
featured listings so the paid-placement cap has something to cap, one practitioner
with two addresses where only one is step-free, and a few with nothing filled in.
"""

from __future__ import annotations

import random
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.contrib.gis.geos import Point
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from directory.models import (
    Approach,
    ClientGroup,
    FundingOption,
    Language,
    Practitioner,
    PractitionerLocation,
    Profession,
    PublicationStatus,
    Qualification,
    Registration,
    SessionFormat,
    Speciality,
    VerificationStatus,
    VerificationType,
)
from directory.services import search_index, verification

#: Fixed so two runs produce the same directory — a demo you can talk about.
SEED = 20260730

# (city, lat, lng, postcode)
PLACES = [
    ("Epsom", 51.3360, -0.2674, "KT18 5EP"),
    ("Guildford", 51.2362, -0.5704, "GU1 3UY"),
    ("Kingston upon Thames", 51.4123, -0.3007, "KT1 1EU"),
    ("Croydon", 51.3762, -0.0982, "CR0 1LB"),
    ("London", 51.5074, -0.1278, "W1G 9QD"),
    ("Reading", 51.4543, -0.9781, "RG1 1JX"),
    ("Brighton", 50.8225, -0.1372, "BN1 1UB"),
    ("Bristol", 51.4545, -2.5879, "BS1 5TR"),
    ("Manchester", 53.4808, -2.2426, "M1 4BT"),
    ("Leeds", 53.8008, -1.5491, "LS1 4AP"),
    ("Birmingham", 52.4862, -1.8904, "B3 2TA"),
    ("Cardiff", 51.4816, -3.1791, "CF10 3AF"),
]

#: Professions that go with each set of post-nominals. Randomising the two
#: independently produced "Dr Aisha Rahman MBBS MRCPsych — Play Therapist", which
#: makes a demo look broken rather than unfinished, and a demo nobody trusts is not
#: doing its job.
PROFESSION_BY_CREDENTIAL = {
    "MBBS MRCPsych": ["consultant-psychiatrist", "psychiatrist"],
    "MBChB MRCPsych": ["consultant-psychiatrist", "psychiatrist"],
    "DClinPsy": ["clinical-psychologist", "counselling-psychologist"],
    "DClinPsy CPsychol": ["clinical-psychologist", "neuropsychologist"],
    "PhD CPsychol": ["counselling-psychologist", "health-psychologist", "forensic-psychologist"],
    "MBACP": ["counsellor"],
    "MBACP (Accred)": ["counsellor"],
    "MBACP (Snr Accred)": ["counsellor"],
    "MUKCP": ["psychotherapist"],
    "MUKCP MBACP": ["psychotherapist", "counsellor"],
    "MBABCP": ["cbt-therapist"],
}

#: Invented names. Deliberately not "Test Practitioner 1" — the cards, the ranking
#: and the alphabetical eye-check are all easier to read with plausible names, and
#: a demo that looks like the real thing is the one that surfaces layout problems.
NAMES = [
    ("Dr", "Aisha Rahman", "MBBS MRCPsych", "female"),
    ("Dr", "Tom Whitfield", "DClinPsy", "male"),
    ("", "Priya Nair", "MBACP (Accred)", "female"),
    ("Dr", "Jordan Bell", "PhD CPsychol", "non_binary"),
    ("Dr", "Eleanor Vance", "MBBS MRCPsych", "female"),
    ("", "Marcus Osei", "MUKCP", "male"),
    ("Dr", "Hannah Lindqvist", "DClinPsy CPsychol", "female"),
    ("", "Deepa Krishnan", "MBACP", "female"),
    ("Dr", "Callum Fraser", "MBChB MRCPsych", "male"),
    ("", "Yusuf Demir", "MBABCP", "male"),
    ("Dr", "Sophie Arundel", "DClinPsy", "female"),
    ("", "Nadia Haddad", "MUKCP MBACP", "female"),
    ("Dr", "Oliver Bankole", "MBBS MRCPsych", "male"),
    ("", "Rachel Stott", "MBACP (Snr Accred)", "female"),
    ("Dr", "Ines Moreau", "PhD CPsychol", "female"),
    ("", "Gareth Pryce", "MBABCP", "male"),
    ("Dr", "Amara Nwosu", "DClinPsy", "female"),
    ("", "Elliot Shaw", "MUKCP", "non_binary"),
    ("Dr", "Farah Siddiqui", "MBBS MRCPsych", "female"),
    ("", "Bryn Davies", "MBACP", "male"),
    ("Dr", "Cecilia Ortiz", "DClinPsy CPsychol", "female"),
    ("", "Kwame Mensah", "MBABCP", "male"),
    ("Dr", "Lucy Fairhurst", "MBChB MRCPsych", "female"),
    ("", "Anya Petrova", "MUKCP", "female"),
    ("Dr", "Samuel Adeyemi", "PhD CPsychol", "male"),
    ("", "Erin Gallagher", "MBACP (Accred)", "female"),
    ("Dr", "Rohan Mehta", "MBBS MRCPsych", "male"),
    ("", "Thea Lindgren", "MUKCP", "female"),
]

INTROS = [
    "I work with adults who have spent years being told they are simply disorganised, "
    "and with families trying to make sense of a new diagnosis.",
    "My work is unhurried and collaborative. Most people come to me after trying to "
    "manage on their own for a long time, and the first thing we do is slow down.",
    "I see adults who are exhausted by anxiety that looks, from the outside, like "
    "coping. We start with what is actually happening in your week.",
    "Much of my practice is with people who have been let down by services before. "
    "I am direct about what I can and cannot help with.",
    "I offer assessment and ongoing support, and I am happy to work alongside your GP "
    "where shared care makes sense.",
    "I work with trauma at whatever pace you can manage, and I will not push you to "
    "talk about things before you are ready.",
]

SERVICES = [
    "Initial consultation, full assessment, written report, and ongoing review "
    "appointments. Letters for employers and universities on request.",
    "Weekly or fortnightly sessions, 50 minutes. Short-term focused work and "
    "open-ended therapy both available.",
    "Assessment, diagnostic feedback, and post-diagnostic support. Medication review "
    "and titration where appropriate, in shared care with your GP.",
    "One-to-one therapy, couples work, and clinical supervision for other practitioners.",
]

WAITS = ["immediate", "short", "medium", "long", ""]


def _fees(rng, *, omit: bool) -> dict:
    """A coherent fee range, in pence, or none at all."""
    if omit:
        return {"fee_min": None, "fee_max": None}
    low = rng.choice([6000, 8000, 9500, 12000, 18000])
    return {"fee_min": low, "fee_max": low + rng.choice([3000, 6000, 12000])}


class Command(BaseCommand):
    help = "Fill a DEVELOPMENT database with searchable demo practitioners."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fresh",
            action="store_true",
            help="Delete existing demo practitioners first (slug prefix 'demo-').",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=len(NAMES),
            help=f"How many to create (max {len(NAMES)}).",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "seed_demo refuses to run with DEBUG off.\n\n"
                "It creates fictional practitioners, several carrying a "
                '"Credentials checked" badge — which on this site is a claim Kiam '
                "makes about a real person's documents. Fake verified clinicians in a "
                "reachable database is worse than having no demo data, so there is no "
                "--force."
            )

        if not Speciality.objects.exists():
            raise CommandError("Run `manage.py seed_taxonomy` first — there is no vocabulary to tag.")

        rng = random.Random(SEED)

        if options["fresh"]:
            deleted, _ = Practitioner.objects.filter(slug__startswith="demo-").delete()
            self.stdout.write(f"Removed {deleted} existing demo objects.")

        vocab = self._vocabulary()
        count = min(options["count"], len(NAMES))
        created = []

        for index in range(count):
            created.append(self._make(index, rng, vocab))

        # The signals keep the vector current on save and on a speciality change, but
        # rebuilding explicitly means a demo database is never one missed signal away
        # from an empty text search.
        rebuilt = search_index.rebuild_all(Practitioner.objects.filter(slug__startswith="demo-"))

        self._report(created, rebuilt)

    # -- data ---------------------------------------------------------------

    def _vocabulary(self) -> dict:
        return {
            "professions": list(Profession.objects.filter(active=True)),
            "specialities": list(Speciality.objects.filter(active=True, implies_minors=False)),
            "minor_specialities": list(Speciality.objects.filter(active=True, implies_minors=True)),
            "approaches": list(Approach.objects.filter(active=True)),
            "languages": list(Language.objects.filter(active=True)),
            "funding": list(FundingOption.objects.filter(active=True)),
            "formats": list(SessionFormat.objects.filter(active=True)),
            "adult_groups": list(ClientGroup.objects.filter(active=True, is_minors=False)),
            "minor_groups": list(ClientGroup.objects.filter(active=True, is_minors=True)),
        }

    def _make(self, index: int, rng: random.Random, vocab: dict) -> dict:
        title, name, post_nominals, gender = NAMES[index]
        slug = "demo-" + name.lower().replace(" ", "-")

        # Deliberate spread rather than uniform randomness, so every branch of the
        # search has at least one row that exercises it.
        online_only = index % 9 == 4
        in_person_only = index % 9 == 7
        is_featured = index in (0, 5, 11, 17)  # four, so the cap of three has to bite
        dbs_state = ["cleared", "provisional", "blocked", "none", "none", "none"][index % 6]
        badge = ["current", "current", "current", "lapsed", "none", "current"][index % 6]
        sparse = index == 13  # one listing with almost nothing filled in

        practitioner = Practitioner.objects.create(
            slug=slug,
            full_name=name,
            display_title=title,
            post_nominals="" if sparse else post_nominals,
            pronouns={"female": "she/her", "male": "he/him", "non_binary": "they/them"}[gender],
            gender=gender,
            profession=self._profession_for(post_nominals, rng, vocab),
            years_experience=rng.choice([2, 4, 6, 9, 12, 15, 18, 22]),
            intro="" if sparse else rng.choice(INTROS),
            services="" if sparse else rng.choice(SERVICES),
            delivery_mode=("online" if online_only else "in_person" if in_person_only else "both"),
            offers_online=not in_person_only,
            online_coverage="UK-wide" if not in_person_only else "",
            is_prescriber=index % 5 == 0,
            public_email=f"{slug}@example.com",
            public_phone=""
            if index % 4 == 1
            else f"0{rng.randint(1000, 1999)} {rng.randint(100000, 999999)}",
            public_website="" if index % 3 else f"https://example.com/{slug}",
            accepting_new_clients=index % 7 != 3,
            typical_wait=WAITS[index % len(WAITS)],
            evening_appointments=index % 3 == 0,
            weekend_appointments=index % 5 == 0,
            availability_note="New assessments open on the first Monday of each month."
            if index % 6 == 0
            else "",
            # Both drawn from ONE ordered pair, not two independent choices. The
            # two lists overlapped, so one demo practitioner in thirty came out
            # with fee_min £180 and fee_max £150 — which the Phase 6 dashboard
            # correctly refused to save, on a page where the practitioner had not
            # touched the fees. Caught by driving the real form.
            **_fees(rng, omit=sparse or index % 8 == 6),
            free_initial_call=index % 4 == 0,
            offers_sliding_scale=index % 6 == 2,
            completeness=45 if sparse else rng.choice([70, 80, 85, 90, 95, 100]),
            last_active_at=timezone.now() - timedelta(days=rng.choice([1, 3, 10, 40, 120])),
            status=PublicationStatus.PUBLISHED,
            published_at=timezone.now() - timedelta(days=rng.randint(5, 400)),
            featured_until=timezone.now() + timedelta(days=30) if is_featured else None,
        )

        # -- taxonomy
        specialities = rng.sample(vocab["specialities"], 1 if sparse else rng.randint(2, 5))
        groups = rng.sample(vocab["adult_groups"], rng.randint(1, 3))
        if dbs_state != "none":
            # Only these get a minor client group, so minor_work_status is derived
            # rather than asserted.
            groups += [rng.choice(vocab["minor_groups"])]
            specialities += [rng.choice(vocab["minor_specialities"])]

        practitioner.specialities.set(specialities)
        practitioner.client_groups.set(groups)
        practitioner.approaches.set(rng.sample(vocab["approaches"], rng.randint(1, 4)))
        practitioner.languages.set(
            [vocab["languages"][0]] + rng.sample(vocab["languages"][1:], rng.randint(0, 2))
        )
        practitioner.funding_options.set(rng.sample(vocab["funding"], rng.randint(1, 4)))
        practitioner.session_formats.set(rng.sample(vocab["formats"], rng.randint(1, 3)))

        # -- locations
        if not online_only:
            self._add_locations(practitioner, index, rng)

        # -- credentials
        if not sparse:
            Qualification.objects.create(
                practitioner=practitioner,
                title=rng.choice(["MBBS", "DClinPsy", "MSc Psychological Therapies", "MA Counselling"]),
                institution=rng.choice(
                    ["King's College London", "University of Manchester", "UCL", "University of Leeds"]
                ),
                year=rng.randint(1998, 2019),
            )
            Registration.objects.create(
                practitioner=practitioner,
                body=rng.choice(["GMC", "HCPC", "BACP", "UKCP", "BABCP"]),
                registration_no=f"DEMO{rng.randint(100000, 999999)}",
                verified=badge == "current",
                register_url="https://example.com/register-entry",
            )

        # -- headshot
        # Two listings deliberately have none, so the placeholder is exercised — but
        # never a featured one, which is the most-looked-at card on the page.
        if is_featured or index % 11 not in (3, 8):
            self._add_headshot(practitioner, name)

        # -- verification, through the real service only
        self._verify(practitioner, badge, dbs_state)

        practitioner.refresh_from_db()
        return {
            "slug": slug,
            "name": f"{title} {name}".strip(),
            "verified": practitioner.is_verified,
            "minor": practitioner.minor_work_status,
            "featured": practitioner.is_featured,
            "locations": practitioner.locations.count(),
        }

    def _profession_for(self, post_nominals: str, rng: random.Random, vocab: dict):
        """A profession the post-nominals could plausibly belong to."""
        slugs = PROFESSION_BY_CREDENTIAL.get(post_nominals)
        if slugs:
            matches = [p for p in vocab["professions"] if p.slug in slugs]
            if matches:
                return rng.choice(matches)
        return rng.choice(vocab["professions"])

    def _add_locations(self, practitioner, index: int, rng: random.Random) -> None:
        city, lat, lng, postcode = PLACES[index % len(PLACES)]
        PractitionerLocation.objects.create(
            practitioner=practitioner,
            label=f"{city} consulting rooms",
            address_line1=f"{rng.randint(1, 200)} {rng.choice(['Worple Road', 'High Street', 'Church Lane', 'Park Road'])}",
            city=city,
            county=rng.choice(["Surrey", "Greater London", "West Yorkshire", ""]),
            postcode=postcode,
            geo=Point(lng, lat, srid=4326),
            is_primary=True,
            days_at_site=rng.choice(["Mon, Wed", "Tue, Thu", "Wed–Fri", ""]),
            step_free_access=index % 3 == 0,
            wheelchair_access=index % 4 == 0,
            parking_available=index % 2 == 0,
            hearing_loop=index % 5 == 0,
            near_public_transport=index % 3 != 2,
        )

        # One practitioner with two addresses where only the FAR one is step-free —
        # the case the search's single-Q location filter exists to get right.
        if index == 2:
            far_city, far_lat, far_lng, far_postcode = PLACES[8]
            PractitionerLocation.objects.create(
                practitioner=practitioner,
                label=f"{far_city} clinic (step-free)",
                address_line1="1 Deansgate",
                city=far_city,
                postcode=far_postcode,
                geo=Point(far_lng, far_lat, srid=4326),
                is_primary=False,
                step_free_access=True,
                parking_available=True,
            )

        # And one with a private address, used for the radius but never displayed.
        if index == 6:
            PractitionerLocation.objects.create(
                practitioner=practitioner,
                label="Home office",
                address_line1="Not for publication",
                city=city,
                postcode=postcode,
                geo=Point(lng + 0.02, lat + 0.02, srid=4326),
                is_primary=False,
                is_public=False,
            )

    def _add_headshot(self, practitioner, name: str) -> None:
        """A flat initials tile, generated locally.

        Not a stock photo and not a network fetch: the cards need *an* image so the
        layout, the lazy-loading split and the LCP attribute are exercised, and a
        recognisable placeholder is honest about being one.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:  # pragma: no cover — Pillow is a hard dependency
            return

        initials = "".join(part[0] for part in name.split()[:2]).upper()
        palette = [(31, 122, 94), (47, 140, 127), (30, 64, 91), (91, 46, 62), (58, 74, 80)]
        colour = palette[sum(map(ord, initials)) % len(palette)]

        image = Image.new("RGB", (320, 320), colour)
        draw = ImageDraw.Draw(image)
        # `load_default(size=…)` rather than a bare `load_default()`: the default
        # bitmap font renders at about 10px, which on a 320px tile is a speck — the
        # first run produced cards with an apparently blank dark square. Sized
        # default font needs no font FILE, so this still works on any machine.
        try:
            font = ImageFont.load_default(size=132)
        except TypeError:  # pragma: no cover — Pillow < 10.1
            font = ImageFont.load_default()
        draw.text((160, 156), initials, fill=(255, 255, 255), anchor="mm", font=font)

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=88)
        practitioner.headshot.save(f"{practitioner.slug}.jpg", ContentFile(buffer.getvalue()), save=True)

    def _verify(self, practitioner, badge: str, dbs_state: str) -> None:
        """Badge and DBS state, derived — never assigned.

        Everything here goes through the same functions the verification workbench
        calls, so a demo badge is backed by the same dated evidence rows a real one
        would be, and `recompute()` decides what the public sees.
        """
        now = timezone.now()

        if badge == "current":
            verification.verify_all_required(
                practitioner,
                actor=None,
                expires_at={VerificationType.INSURANCE: now + timedelta(days=300)},
                notes="Demo data.",
            )
        elif badge == "lapsed":
            # Verified once, insurance since expired. The nightly sweep is what
            # normally flips this; doing it by evidence keeps the state honest.
            verification.verify_all_required(
                practitioner,
                actor=None,
                expires_at={VerificationType.INSURANCE: now + timedelta(days=1)},
                notes="Demo data.",
            )
            practitioner.verifications.filter(type=VerificationType.INSURANCE).update(
                expires_at=now - timedelta(days=2), status=VerificationStatus.EXPIRED
            )
            verification.recompute(practitioner)

        if dbs_state == "cleared":
            check, _ = practitioner.verifications.get_or_create(type=VerificationType.DBS)
            verification.set_check_status(
                check,
                status=VerificationStatus.VERIFIED,
                actor=None,
                expires_at=now + timedelta(days=900),
            )
        elif dbs_state == "provisional":
            verification.grant_provisional_dbs(practitioner, actor=None, note="Demo data.")
        elif dbs_state == "blocked":
            # A minor client group and no DBS at all — recompute() derives BLOCKED.
            verification.recompute(practitioner)

    # -- output -------------------------------------------------------------

    def _report(self, created: list[dict], rebuilt: int) -> None:
        self.stdout.write("")
        self.stdout.write(f"{'slug':34} {'badge':7} {'under-18':12} {'featured':9} locations")
        self.stdout.write("-" * 76)
        for row in created:
            self.stdout.write(
                f"{row['slug']:34} "
                f"{'yes' if row['verified'] else 'no':7} "
                f"{row['minor']:12} "
                f"{'PAID' if row['featured'] else '-':9} "
                f"{row['locations']}"
            )

        verified = sum(1 for r in created if r["verified"])
        featured = sum(1 for r in created if r["featured"])
        by_state: dict[str, int] = {}
        for row in created:
            by_state[row["minor"]] = by_state.get(row["minor"], 0) + 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{len(created)} published practitioners."))
        self.stdout.write(f"  badge current      {verified}")
        self.stdout.write(f"  featured (paid)    {featured}  (the page caps the block at 3)")
        for state, total in sorted(by_state.items()):
            self.stdout.write(f"  under-18 {state:12} {total}")
        self.stdout.write(f"  search vectors     {rebuilt} rebuilt")
        self.stdout.write("")
        self.stdout.write("Try:")
        self.stdout.write("  /search/")
        self.stdout.write("  /search/?near=KT18+5EP&radius=10")
        self.stdout.write("  /search/?group=adolescents          (CLEARED only)")
        self.stdout.write("  /search/?step_free=1&near=KT18+5EP  (same address must satisfy both)")
        self.stdout.write("  /search/?q=ADHD&verified=1")
