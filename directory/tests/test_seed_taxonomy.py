"""``seed_taxonomy`` is idempotent, and never deletes a term in use.

It runs on every deploy, so "safe to re-run" is not a nice-to-have. The tests
that matter are the second-run ones: counts stable, no duplicates, and nothing
detached from a practitioner who had selected it.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from directory import taxonomy
from directory.factories import PractitionerFactory
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

pytestmark = pytest.mark.django_db

MODELS = [
    Profession,
    SpecialityCategory,
    Speciality,
    Approach,
    ClientGroup,
    SessionFormat,
    FundingOption,
    Language,
]


def _counts():
    return {model.__name__: model.objects.count() for model in MODELS}


def test_seeding_creates_the_whole_vocabulary():
    call_command("seed_taxonomy", verbosity=0)

    assert Profession.objects.count() == len(taxonomy.PROFESSIONS)
    assert SpecialityCategory.objects.count() == len(taxonomy.SPECIALITY_CATEGORIES)
    assert Speciality.objects.count() == sum(len(s) for _, _, s in taxonomy.SPECIALITY_CATEGORIES)
    assert Approach.objects.count() == len(taxonomy.APPROACHES)
    assert ClientGroup.objects.count() == len(taxonomy.CLIENT_GROUPS)
    assert SessionFormat.objects.count() == len(taxonomy.SESSION_FORMATS)
    assert FundingOption.objects.count() == len(taxonomy.FUNDING_OPTIONS)
    assert Language.objects.count() == len(taxonomy.LANGUAGES)


def test_running_twice_changes_nothing():
    """The whole point. Counts stable, no duplicates."""
    call_command("seed_taxonomy", verbosity=0)
    first = _counts()

    call_command("seed_taxonomy", verbosity=0)
    second = _counts()

    assert first == second


def test_running_three_times_still_changes_nothing():
    for _ in range(3):
        call_command("seed_taxonomy", verbosity=0)

    assert Speciality.objects.count() == sum(len(s) for _, _, s in taxonomy.SPECIALITY_CATEGORIES)


def test_no_duplicate_slugs_anywhere():
    call_command("seed_taxonomy", verbosity=0)
    call_command("seed_taxonomy", verbosity=0)

    for model in MODELS:
        key = "code" if model is Language else "slug"
        values = list(model.objects.values_list(key, flat=True))
        assert len(values) == len(set(values)), f"{model.__name__} has duplicate {key}s"


def test_a_second_run_corrects_a_drifted_name():
    """Names are allowed to change in taxonomy.py; the slug is the identity."""
    call_command("seed_taxonomy", verbosity=0)

    speciality = Speciality.objects.first()
    original = speciality.name
    Speciality.objects.filter(pk=speciality.pk).update(name="Something a human typed")

    call_command("seed_taxonomy", verbosity=0)

    speciality.refresh_from_db()
    assert speciality.name == original


def test_a_term_removed_from_taxonomy_is_deactivated_not_deleted(monkeypatch):
    """Deleting would break the FK, or silently drop a practitioner's selection."""
    call_command("seed_taxonomy", verbosity=0)

    doomed = Approach.objects.first()
    remaining = [a for a in taxonomy.APPROACHES if a[0] != doomed.slug]
    monkeypatch.setattr(taxonomy, "APPROACHES", remaining)

    call_command("seed_taxonomy", verbosity=0)

    doomed.refresh_from_db()
    assert doomed.active is False
    assert Approach.objects.filter(pk=doomed.pk).exists()


def test_a_deactivated_term_keeps_its_practitioners(monkeypatch):
    """The reason deletion is off the table."""
    call_command("seed_taxonomy", verbosity=0)

    approach = Approach.objects.first()
    practitioner = PractitionerFactory()
    practitioner.approaches.add(approach)

    remaining = [a for a in taxonomy.APPROACHES if a[0] != approach.slug]
    monkeypatch.setattr(taxonomy, "APPROACHES", remaining)
    call_command("seed_taxonomy", verbosity=0)

    assert practitioner.approaches.filter(pk=approach.pk).exists()


def test_a_returning_term_is_reactivated():
    call_command("seed_taxonomy", verbosity=0)

    approach = Approach.objects.first()
    Approach.objects.filter(pk=approach.pk).update(active=False)

    call_command("seed_taxonomy", verbosity=0)

    approach.refresh_from_db()
    assert approach.active is True


def test_sort_order_follows_list_position():
    """taxonomy.py is the source of truth for display order too."""
    call_command("seed_taxonomy", verbosity=0)

    first_slug = taxonomy.PROFESSIONS[0][0]
    third_slug = taxonomy.PROFESSIONS[2][0]

    assert Profession.objects.get(slug=first_slug).sort_order == 0
    assert Profession.objects.get(slug=third_slug).sort_order == 2


def test_specialities_are_attached_to_their_category():
    call_command("seed_taxonomy", verbosity=0)

    cat_slug, _, specialities = taxonomy.SPECIALITY_CATEGORIES[0]
    category = SpecialityCategory.objects.get(slug=cat_slug)

    assert category.specialities.count() == len(specialities)


def test_a_speciality_moving_category_keeps_its_slug(monkeypatch):
    """Moving must not create a second row — the slug is in URLs and listings."""
    call_command("seed_taxonomy", verbosity=0)

    moving_slug = taxonomy.SPECIALITY_CATEGORIES[0][2][0][0]
    before = Speciality.objects.get(slug=moving_slug)
    original_pk = before.pk

    # Move that speciality into the second category.
    cats = [list(c) for c in taxonomy.SPECIALITY_CATEGORIES]
    entry = cats[0][2][0]
    cats[0][2] = cats[0][2][1:]
    cats[1][2] = [entry, *cats[1][2]]
    monkeypatch.setattr(taxonomy, "SPECIALITY_CATEGORIES", [tuple(c) for c in cats])

    call_command("seed_taxonomy", verbosity=0)

    after = Speciality.objects.get(slug=moving_slug)
    assert after.pk == original_pk
    assert after.category.slug == taxonomy.SPECIALITY_CATEGORIES[1][0]


def test_minor_client_groups_are_flagged():
    """is_minors drives the DBS requirement and the under-18 search gate."""
    call_command("seed_taxonomy", verbosity=0)

    assert ClientGroup.objects.get(slug="children").is_minors is True
    assert ClientGroup.objects.get(slug="adolescents").is_minors is True
    assert ClientGroup.objects.get(slug="adults").is_minors is False


def test_restricted_professions_carry_required_bodies():
    """A restricted title cannot publish without a matching verified registration."""
    call_command("seed_taxonomy", verbosity=0)

    psychiatrist = Profession.objects.get(slug="consultant-psychiatrist")
    assert psychiatrist.restricted is True
    assert "GMC" in psychiatrist.required_bodies


def test_no_taxonomy_label_names_a_prescription_only_medicine():
    """docs/content-compliance.md §1 — enforced by construction, asserted here.

    The permitted generic terms are the only medication language allowed.
    """
    call_command("seed_taxonomy", verbosity=0)

    # A sample of common UK ADHD/psychiatric POM substance and brand names. Not
    # the full BNF list — that lives server-side in settings.POM_DICTIONARY_PATH
    # — but enough that an accidental addition to taxonomy.py trips here.
    banned = [
        "methylphenidate",
        "lisdexamfetamine",
        "dexamfetamine",
        "atomoxetine",
        "guanfacine",
        "elvanse",
        "concerta",
        "ritalin",
        "medikinet",
        "strattera",
        "sertraline",
        "fluoxetine",
        "citalopram",
        "venlafaxine",
        "mirtazapine",
        "quetiapine",
        "olanzapine",
        "risperidone",
        "aripiprazole",
        "lithium",
        "diazepam",
        "zopiclone",
        "melatonin",
        "pregabalin",
    ]

    labels = []
    for model in MODELS:
        labels += [n.lower() for n in model.objects.values_list("name", flat=True)]
    for synonyms in Speciality.objects.values_list("synonyms", flat=True):
        labels += [s.lower() for s in (synonyms or [])]

    haystack = " ".join(labels)
    found = [drug for drug in banned if drug in haystack]

    assert not found, f"Prescription-only medicine named in the taxonomy: {found}"


def test_no_taxonomy_label_makes_an_efficacy_claim():
    """docs/content-compliance.md §2."""
    call_command("seed_taxonomy", verbosity=0)

    labels = []
    for model in MODELS:
        labels += [n.lower() for n in model.objects.values_list("name", flat=True)]
    haystack = " ".join(labels)

    found = [flag for flag in taxonomy.EFFICACY_CLAIM_FLAGS if flag.lower() in haystack]
    assert not found, f"Efficacy claim in the taxonomy: {found}"


def test_clinical_descriptions_are_left_empty_pending_sign_off():
    """Speciality/category descriptions are clinical content (Dr. Abbass)."""
    call_command("seed_taxonomy", verbosity=0)

    assert not Speciality.objects.exclude(description="").exists()
    assert not SpecialityCategory.objects.exclude(description="").exists()


def test_dry_run_writes_nothing():
    call_command("seed_taxonomy", "--dry-run", verbosity=0)

    assert Profession.objects.count() == 0
    assert Speciality.objects.count() == 0
