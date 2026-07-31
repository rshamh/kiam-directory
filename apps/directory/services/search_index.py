"""Maintaining ``Practitioner.search_vector``.

Weighted A–D, per ``docs/architecture.md``:

    A  full_name                     the thing people actually type
    B  speciality names + synonyms   what they treat, plus "add" -> "ADHD"
    C  intro                         the first-paragraph answer
    D  services                      the long tail

Synonyms sit in band B alongside the speciality names deliberately: they are the
whole reason a search for "add" or "asperger" finds anything, and demoting them
to C would put them below the practitioner's own prose.

**The M2M problem.** A practitioner's specialities are a ManyToMany, and changing
one does NOT fire ``post_save`` on the Practitioner. Tagging a listing with "Adult
ADHD assessment" would leave a search vector that has never heard of ADHD, and the
listing would simply not be findable — silently, with nothing in a log. So there
are two signals, not one, and the ``m2m_changed`` half is the one that matters.

Rebuilds go through ``rebuild()`` in every case, so there is exactly one
definition of the weighting.
"""

from __future__ import annotations

import logging

from django.contrib.postgres.search import SearchVector
from django.db.models import TextField, Value
from django.db.models.functions import Coalesce

logger = logging.getLogger("directory.search_index")

CONFIG = "english"


def _text(field_name):
    """A NULL-safe TextField reference to one of the practitioner's own columns."""
    return Coalesce(field_name, Value(""), output_field=TextField())


def _speciality_text(practitioner) -> str:
    """Speciality names and their synonyms, flattened to one string.

    Resolved in Python rather than as a join, because Postgres does not allow a
    joined field reference inside an UPDATE — a SearchVector over
    ``specialities__name`` raises "Joined field references are not permitted in
    this query". Synonyms could not be joined anyway: they are an array column,
    and SearchVector cannot traverse one.

    Synonyms share band B with the names deliberately — they are the whole reason
    "add" finds "ADHD" and "asperger" finds an autism assessment.
    """
    terms: list[str] = []
    for name, synonyms in practitioner.specialities.values_list("name", "synonyms"):
        terms.append(name or "")
        terms.extend(synonyms or [])
    return " ".join(t for t in terms if t)


def rebuild(practitioner) -> None:
    """Recompute one practitioner's search vector.

    Own columns are referenced by name so the index reflects what is actually in
    the row; the speciality text is passed as a literal because of the join
    restriction above.

    ``Coalesce(..., Value(""))`` on every column because a SearchVector over a
    NULL column yields NULL for the WHOLE concatenated vector, not just that
    part — one empty ``intro`` would otherwise wipe the name out of the index.

    ``output_field`` is explicit because ``full_name`` is a CharField and
    ``intro`` a TextField, and Django refuses to guess across the two
    ("Expression contains mixed types").
    """
    from ..models import Practitioner

    Practitioner.objects.filter(pk=practitioner.pk).update(
        search_vector=(
            SearchVector(_text("full_name"), weight="A", config=CONFIG)
            + SearchVector(Value(_speciality_text(practitioner)), weight="B", config=CONFIG)
            + SearchVector(_text("intro"), weight="C", config=CONFIG)
            + SearchVector(_text("services"), weight="D", config=CONFIG)
        )
    )


def rebuild_all(queryset=None) -> int:
    """Rebuild every practitioner. The nightly safety net.

    Deliberately per-row rather than one bulk UPDATE: the synonym array has to be
    flattened in Python, and a nightly job that is correct beats one that is fast.
    """
    from ..models import Practitioner

    queryset = Practitioner.objects.all() if queryset is None else queryset
    count = 0
    # chunk_size is required once prefetch_related is in play, and is what keeps
    # this from loading the whole table into memory on a nightly full rebuild.
    for practitioner in queryset.prefetch_related("specialities").iterator(chunk_size=500):
        rebuild(practitioner)
        count += 1

    logger.info("search_index.rebuilt", extra={"practitioners": count})
    return count
