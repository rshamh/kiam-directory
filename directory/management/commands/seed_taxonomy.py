"""Load the controlled vocabulary from ``directory/taxonomy.py``.

Idempotent and safe to re-run — that is the whole design constraint, because this
runs on every deploy and the vocabulary grows over time.

Two rules that make it safe:

**``update_or_create`` keyed on slug.** The slug is the stable identity; names and
sort orders are allowed to change and will be corrected on the next run. Nothing
is matched on name, because renaming a term must not silently create a second one.

**Nothing is ever deleted.** A term a practitioner has already selected cannot be
removed without either breaking the FK or silently dropping their data — and a
`Speciality` is referenced by `PROTECT` from its category, and M2M'd from
`Practitioner`. A term that disappears from ``taxonomy.py`` is marked
``active=False`` instead, which takes it out of the filter sidebar and the
submission form while leaving every existing listing intact. Reactivation is
automatic if it comes back.

``sort_order`` is assigned from list position, so ``taxonomy.py`` is the single
source of truth for display order too — reordering the list reorders the UI.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from directory import taxonomy
from directory.models import (
    Approach,
    ClientGroup,
    FundingOption,
    Language,
    Profession,
    SessionFormat,
    Speciality,
    SpecialityCategory,
)


class Command(BaseCommand):
    help = "Seed or update the controlled vocabulary. Idempotent; safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.dry_run = options["dry_run"]
        self.stats: dict[str, dict[str, int]] = {}

        self._seed_professions()
        self._seed_specialities()
        self._seed_approaches()
        self._seed_client_groups()
        self._seed_session_formats()
        self._seed_funding_options()
        self._seed_languages()

        self._report()

        if self.dry_run:
            # Roll the whole thing back rather than trying to predict changes: the
            # counts above are then measured, not estimated.
            transaction.set_rollback(True)
            self.stdout.write(self.style.WARNING("\nDry run — nothing was written."))

    # -- helpers ------------------------------------------------------------

    def _track(self, model, created: bool) -> None:
        bucket = self.stats.setdefault(model.__name__, {"created": 0, "updated": 0, "deactivated": 0})
        bucket["created" if created else "updated"] += 1

    def _deactivate_missing(self, model, seen_slugs: set[str], *, field: str = "slug") -> None:
        """Mark anything no longer in taxonomy.py inactive. Never delete."""
        stale = model.objects.filter(active=True).exclude(**{f"{field}__in": seen_slugs})
        count = stale.count()
        if count:
            stale.update(active=False)
            bucket = self.stats.setdefault(model.__name__, {"created": 0, "updated": 0, "deactivated": 0})
            bucket["deactivated"] += count

    # -- the seven vocabularies ---------------------------------------------

    def _seed_professions(self) -> None:
        seen = set()
        for order, (slug, name, restricted, bodies) in enumerate(taxonomy.PROFESSIONS):
            _, created = Profession.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "restricted": restricted,
                    "required_bodies": list(bodies),
                    "sort_order": order,
                    "active": True,
                },
            )
            self._track(Profession, created)
            seen.add(slug)
        self._deactivate_missing(Profession, seen)

    def _seed_specialities(self) -> None:
        seen_categories, seen_specialities = set(), set()

        for cat_order, (cat_slug, cat_name, specialities) in enumerate(taxonomy.SPECIALITY_CATEGORIES):
            category, created = SpecialityCategory.objects.update_or_create(
                slug=cat_slug,
                defaults={"name": cat_name, "sort_order": cat_order, "active": True},
            )
            self._track(SpecialityCategory, created)
            seen_categories.add(cat_slug)

            for order, (slug, name, synonyms, implies_minors) in enumerate(specialities):
                # `category` is in defaults, not the lookup: a speciality that moves
                # between categories keeps its slug, its listings and its inbound
                # links rather than becoming a second row.
                _, created = Speciality.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "category": category,
                        "name": name,
                        "synonyms": list(synonyms),
                        "implies_minors": implies_minors,
                        "sort_order": order,
                        "active": True,
                    },
                )
                self._track(Speciality, created)
                seen_specialities.add(slug)

        self._deactivate_missing(SpecialityCategory, seen_categories)
        self._deactivate_missing(Speciality, seen_specialities)

    def _seed_approaches(self) -> None:
        seen = set()
        for order, (slug, name, abbreviation) in enumerate(taxonomy.APPROACHES):
            _, created = Approach.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "abbreviation": abbreviation,
                    "sort_order": order,
                    "active": True,
                },
            )
            self._track(Approach, created)
            seen.add(slug)
        self._deactivate_missing(Approach, seen)

    def _seed_client_groups(self) -> None:
        seen = set()
        for order, (slug, name, min_age, max_age, is_minors) in enumerate(taxonomy.CLIENT_GROUPS):
            _, created = ClientGroup.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "min_age": min_age,
                    "max_age": max_age,
                    # Load-bearing: this drives the enhanced-DBS requirement and the
                    # under-18 search gate (docs/content-compliance.md §4).
                    "is_minors": is_minors,
                    "sort_order": order,
                    "active": True,
                },
            )
            self._track(ClientGroup, created)
            seen.add(slug)
        self._deactivate_missing(ClientGroup, seen)

    def _seed_session_formats(self) -> None:
        seen = set()
        for order, (slug, name) in enumerate(taxonomy.SESSION_FORMATS):
            _, created = SessionFormat.objects.update_or_create(
                slug=slug, defaults={"name": name, "sort_order": order, "active": True}
            )
            self._track(SessionFormat, created)
            seen.add(slug)
        self._deactivate_missing(SessionFormat, seen)

    def _seed_funding_options(self) -> None:
        seen = set()
        for order, (slug, name, group) in enumerate(taxonomy.FUNDING_OPTIONS):
            _, created = FundingOption.objects.update_or_create(
                slug=slug,
                defaults={"name": name, "group": group, "sort_order": order, "active": True},
            )
            self._track(FundingOption, created)
            seen.add(slug)
        self._deactivate_missing(FundingOption, seen)

    def _seed_languages(self) -> None:
        # Language is keyed on `code`, not `slug` — it has no slug field.
        seen = set()
        for order, (code, name) in enumerate(taxonomy.LANGUAGES):
            _, created = Language.objects.update_or_create(
                code=code, defaults={"name": name, "sort_order": order, "active": True}
            )
            self._track(Language, created)
            seen.add(code)
        self._deactivate_missing(Language, seen, field="code")

    # -- output -------------------------------------------------------------

    def _report(self) -> None:
        for model_name in sorted(self.stats):
            counts = self.stats[model_name]
            line = (
                f"{model_name:<20} "
                f"created {counts['created']:>3}  "
                f"updated {counts['updated']:>3}  "
                f"deactivated {counts['deactivated']:>3}"
            )
            style = self.style.SUCCESS if counts["created"] else self.style.HTTP_INFO
            self.stdout.write(style(line))
