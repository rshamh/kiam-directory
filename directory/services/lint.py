"""Submission lint. Runs at submit, before a human sees anything.

Five rules, from ``docs/content-compliance.md``. Two of them refuse the
submission outright; three accept it but stop it auto-publishing and put the
reason in front of a reviewer.

    BLOCK   the practitioner cannot submit until they change something
    HOLD    submitted, but a human must clear it before it goes live

| Rule | Severity | Why |
|---|---|---|
| `pom` | BLOCK | UK ASA/CAP prohibits advertising prescription-only medicines to the public (§1) |
| `contact` | BLOCK | A listing nobody can contact is not a listing |
| `efficacy` | HOLD | "cure", "guaranteed", "proven to" — §2 |
| `child_work` | HOLD | Free text advertising work with under-18s while `minor_work_status != CLEARED` — §4 |
| `restricted_title` | HOLD | A protected title claimed without a matching verified registration — §3 |

**Why `restricted_title` holds rather than blocks.** Verification happens *after*
review: at submission a practitioner has no verified registration yet, by
definition. Blocking here would make it impossible to ever submit. §3's "cannot
publish" is enforced where it belongs — in
``backoffice.services.publication.approve()``, which refuses to publish a
restricted-title practitioner without one. The lint's job is to put it in front
of the reviewer before they get there.

**Why matching is word-boundary and case-insensitive.** A naive substring search
flags "add" inside "additional" and "sad" inside "sadness", and a reviewer who
sees three false positives stops reading the fourth. Multi-word terms are matched
with flexible whitespace so "sodium  valproate" across a line break still hits.

The result serialises into ``ReviewRequest.lint_flags``, which is what the review
queue renders and what the audit trail keeps.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from ..taxonomy import CHILD_WORK_FLAGS, EFFICACY_CLAIM_FLAGS, RESTRICTED_TITLE_FLAGS

logger = logging.getLogger("directory.lint")

BLOCK = "block"
HOLD = "hold"

#: The free-text fields a practitioner controls. Everything else on the profile
#: is either a controlled vocabulary or a structured field.
LINTED_FIELDS = ("intro", "services", "availability_note")

#: Additionally scanned for RESTRICTED TITLES only.
#:
#: §3 scopes the restricted-title rule to `Profession`, but `post_nominals` and
#: `display_title` are free text that renders next to the practitioner's name —
#: profession "Counsellor" (unrestricted) plus post_nominals "Clinical
#: Psychologist" publishes a protected title past both the profession gate and a
#: bio-only scan. They are not linted for POM or efficacy because they are short
#: credential strings, not prose.
TITLE_FIELDS = ("post_nominals", "display_title")


@dataclass
class Finding:
    rule: str
    severity: str
    field: str
    matches: list[str]
    message: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "field": self.field,
            "matches": self.matches,
            "message": self.message,
        }


@dataclass
class LintResult:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == BLOCK]

    @property
    def holds(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == HOLD]

    @property
    def is_blocked(self) -> bool:
        """Submission is refused."""
        return bool(self.blocks)

    @property
    def must_hold_for_review(self) -> bool:
        """Submitted, but must not auto-publish."""
        return bool(self.holds)

    def as_dict(self) -> dict:
        """The shape stored in ``ReviewRequest.lint_flags``."""
        return {
            "blocked": self.is_blocked,
            "held": self.must_hold_for_review,
            "findings": [f.as_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _pattern(terms) -> re.Pattern | None:
    """One alternation over every term, word-bounded and case-insensitive.

    A single compiled pattern rather than a loop: the POM dictionary is ~150
    terms and this runs on every submission.

    ``\\s+`` between the words of a multi-word term so "sodium valproate" still
    matches across a newline or a double space.
    """
    if not terms:
        return None
    parts = [r"\s+".join(re.escape(word) for word in term.split()) for term in terms if term.strip()]
    if not parts:
        return None
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.IGNORECASE)


def _find(text: str, pattern: re.Pattern | None) -> list[str]:
    """Distinct matches, lowercased, in first-seen order."""
    if not text or pattern is None:
        return []
    seen, out = set(), []
    for match in pattern.finditer(text):
        term = " ".join(match.group(0).split()).lower()
        if term not in seen:
            seen.add(term)
            out.append(term)
    return out


@functools.lru_cache(maxsize=1)
def _pom_terms_cached(path: str, mtime: float) -> tuple[str, ...]:
    """Parsed dictionary, keyed on path AND mtime.

    The mtime in the cache key is what makes "updates without a deploy" true:
    edit the file and the next submission re-reads it, but an unchanged file is
    parsed once per process rather than on every submit.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return tuple(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))


def pom_terms() -> tuple[str, ...]:
    path = getattr(settings, "POM_DICTIONARY_PATH", "")
    if not path or not Path(path).exists():
        # Fail LOUD, not open. An empty dictionary silently permits exactly the
        # thing the rule exists to prevent, and nobody would notice until the ASA
        # did. Better a broken submit path than a quietly disabled control.
        raise ImproperlyConfiguredPOMDictionary(
            f"POM_DICTIONARY_PATH is unset or missing ({path!r}). The submission lint "
            "cannot check for prescription-only medicine names without it."
        )
    return _pom_terms_cached(str(path), Path(path).stat().st_mtime)


class ImproperlyConfiguredPOMDictionary(RuntimeError):
    """The POM dictionary is missing. Submissions cannot be linted safely."""


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------


def _check_pom(practitioner) -> list[Finding]:
    """§1 — no prescription-only medicine named in public copy. BLOCKS."""
    pattern = _pattern(pom_terms())
    findings = []
    for field_name in LINTED_FIELDS:
        matches = _find(getattr(practitioner, field_name, "") or "", pattern)
        if matches:
            findings.append(
                Finding(
                    rule="pom",
                    severity=BLOCK,
                    field=field_name,
                    matches=matches,
                    message=(
                        "This names a prescription-only medicine, which UK advertising rules "
                        "do not allow on a public page. Please describe the service instead — "
                        "for example “medication management”, “prescribing”, “titration and "
                        "review” or “shared care”."
                    ),
                )
            )
    return findings


def _check_efficacy(practitioner) -> list[Finding]:
    """§2 — no cure, guarantee or outcome claims. HOLDS."""
    pattern = _pattern(EFFICACY_CLAIM_FLAGS)
    findings = []
    for field_name in LINTED_FIELDS:
        matches = _find(getattr(practitioner, field_name, "") or "", pattern)
        if matches:
            findings.append(
                Finding(
                    rule="efficacy",
                    severity=HOLD,
                    field=field_name,
                    matches=matches,
                    message=(
                        "This reads as a claim about outcomes. Wording like this needs a "
                        "reviewer to look at it before the listing goes live."
                    ),
                )
            )
    return findings


def _check_child_work(practitioner) -> list[Finding]:
    """§4 — free text advertising under-18 work without a cleared DBS. HOLDS.

    The residual risk the client-group gate cannot cover: a practitioner whose
    minor client groups are correctly hidden, whose bio still says "I work with
    children and teenagers".
    """
    from ..models import MinorWorkStatus

    if practitioner.minor_work_status == MinorWorkStatus.CLEARED:
        return []

    pattern = _pattern(CHILD_WORK_FLAGS)
    findings = []
    for field_name in LINTED_FIELDS:
        matches = _find(getattr(practitioner, field_name, "") or "", pattern)
        if matches:
            findings.append(
                Finding(
                    rule="child_work",
                    severity=HOLD,
                    field=field_name,
                    matches=matches,
                    message=(
                        "This describes work with under-18s, but this listing is not cleared "
                        "for under-18 work (enhanced DBS not verified). A reviewer must check "
                        "it before publication."
                    ),
                )
            )
    return findings


def _check_restricted_title(practitioner) -> list[Finding]:
    """§3 — a protected title claimed in free text. HOLDS.

    Flags the *claim*; whether the practitioner is entitled to it is a
    verification question, and publication is refused separately by
    backoffice.services.publication.approve(). See the module docstring.
    """
    verified_bodies = set(practitioner.registrations.filter(verified=True).values_list("body", flat=True))

    pattern = _pattern(RESTRICTED_TITLE_FLAGS)
    findings = []
    for field_name in (*LINTED_FIELDS, *TITLE_FIELDS):
        matches = _find(getattr(practitioner, field_name, "") or "", pattern)
        if not matches:
            continue
        findings.append(
            Finding(
                rule="restricted_title",
                severity=HOLD,
                field=field_name,
                matches=matches,
                message=(
                    "This uses a protected professional title. A reviewer must confirm a "
                    "verified registration with the relevant regulator before publication. "
                    + (
                        f"Verified registrations on file: {', '.join(sorted(verified_bodies))}."
                        if verified_bodies
                        else "No verified registration is on file yet."
                    )
                ),
            )
        )
    return findings


def _check_contact(practitioner) -> list[Finding]:
    """At least one way to reach them. BLOCKS.

    Kiam is an introducer: the entire point of a listing is that a client can
    contact the practitioner directly. One with no contact route is not a
    listing, it is an advert.
    """
    has_contact = any(
        (getattr(practitioner, name, "") or "").strip()
        for name in ("public_email", "public_phone", "public_website", "booking_url")
    )
    if has_contact:
        return []

    return [
        Finding(
            rule="contact",
            severity=BLOCK,
            field="public_email",
            matches=[],
            message=(
                "Add at least one way for clients to contact you — an email address, a phone "
                "number, a website or a booking link. Clients arrange appointments with you "
                "directly, so a listing needs at least one of these."
            ),
        )
    ]


RULES = (
    _check_pom,
    _check_contact,
    _check_efficacy,
    _check_child_work,
    _check_restricted_title,
)


def run(practitioner) -> LintResult:
    """Lint a practitioner's free text and contact details.

    Called at submission by ``backoffice.services.review.submit()``. Pure — it
    reads the practitioner and returns findings, and writes nothing.
    """
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(practitioner))

    result = LintResult(findings=findings)

    if findings:
        logger.info(
            "lint.findings",
            extra={
                "practitioner_id": str(practitioner.pk),
                "blocked": result.is_blocked,
                "rules": sorted({f.rule for f in findings}),
            },
        )
    return result
