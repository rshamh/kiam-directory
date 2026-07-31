"""The dashboard landing page: where your listing stands, and what to do next.

Everything here is read-only. Nothing in this module writes a verification field,
a publication status or a completeness score — it reads what the services that own
those have already decided and puts it into sentences.

**Publication status is translated, not displayed.** ``PublicationStatus`` labels
are written for staff ("In review", "Unpublished (consent withdrawn)"). A
practitioner needs to know whether strangers can see their page right now, and if
not, whose move it is. So each status maps to a plain sentence, a "who is waiting
on whom" line, and whether the page is currently public.

**The verification panel is per-check, because that is how it lapses.** The badge
is all-or-nothing but its evidence is not: insurance expires annually and takes the
badge with it while photo ID sits verified forever. A practitioner told only
"unverified" cannot tell which certificate to go and find. So every required check
is listed with its own state and its own expiry, and the reminder thresholds from
``docs/verification-policy.md`` (60/30/7/0 days) are applied per check.

TODO(sign-off): Dr. Abbass — everything in ``STATUS_COPY``, ``minor_work()`` and
the check panel is public copy derived from ``docs/verification-policy.md``, which
§8 of ``docs/content-compliance.md`` puts behind a clinical gate. Being behind a
login narrows who reads it, not what it asserts: this is the copy that determines
whether a practitioner understands the under-18 gate and the DBS window, which is
a higher-consequence audience than an anonymous browser, not a lower one.

**The provisional-DBS countdown is worded from the brief, verbatim.** It is the one
piece of copy on this page that a practitioner may act on wrongly if it is vague:
they are live, they are earning, and the thing that is missing is a safeguarding
check. It says which parts of their listing are and are not showing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.directory.models import (
    MinorWorkStatus,
    PublicationStatus,
    VerificationStatus,
    VerificationType,
)
from apps.directory.services import completeness, verification

#: Days before an expiry at which the dashboard starts warning.
#: ``docs/verification-policy.md``: "Reminders at 60 / 30 / 7 / 0 days before
#: expiry: dashboard banner plus email." The email half is
#: ``verification.nightly_sweep()``; this is the banner half, and it reads the same
#: constant so the two cannot drift.
REMINDER_DAYS = tuple(verification.REMINDER_DAYS)


@dataclass(frozen=True)
class Status:
    """Publication state, for someone who did not write the state machine."""

    #: Whether a stranger can read the listing right now.
    is_public: bool
    headline: str
    detail: str
    #: Whose move it is. Empty when nobody is waiting on anything.
    waiting_on: str = ""
    tone: str = "neutral"  # neutral | good | warning | blocked


#: One row per ``PublicationStatus``. A missing key would render an empty panel on
#: the one page that is supposed to answer "is my listing live?", so
#: ``test_every_publication_status_has_plain_language`` asserts the mapping is total.
STATUS_COPY: dict[str, Status] = {
    PublicationStatus.DRAFT: Status(
        is_public=False,
        headline="Your listing is a draft",
        detail="Nobody can see it yet. Fill it in, then send it to us to check.",
        waiting_on="you",
    ),
    PublicationStatus.SUBMITTED: Status(
        is_public=False,
        headline="Your listing is with us",
        detail="We have it and will check the details and your evidence.",
        waiting_on="us",
    ),
    PublicationStatus.IN_REVIEW: Status(
        is_public=False,
        headline="We are checking your listing",
        detail="Somebody here is reading it now.",
        waiting_on="us",
    ),
    PublicationStatus.CHANGES_REQUESTED: Status(
        is_public=False,
        headline="We have asked for some changes",
        detail="Have a look at the notes below, make the changes, and send it back.",
        waiting_on="you",
        tone="warning",
    ),
    PublicationStatus.APPROVED: Status(
        is_public=False,
        headline="Approved, going live shortly",
        detail="Your listing has been approved and is about to be published.",
        waiting_on="us",
        tone="good",
    ),
    PublicationStatus.PUBLISHED: Status(
        is_public=True,
        headline="Your listing is live",
        detail="Anyone can find it in the directory and contact you directly.",
        tone="good",
    ),
    PublicationStatus.SUSPENDED: Status(
        is_public=False,
        headline="Your listing has been taken down",
        detail=(
            "It is not visible to anyone at the moment. We have emailed you about "
            "this — please reply to that email if anything is unclear."
        ),
        waiting_on="us",
        tone="blocked",
    ),
    PublicationStatus.UNPUBLISHED: Status(
        is_public=False,
        headline="You have taken your listing down",
        detail=(
            "You withdrew your consent to be listed, so your page is no longer "
            "public. You can ask us to put it back at any time."
        ),
        waiting_on="you",
    ),
    PublicationStatus.REMOVED: Status(
        is_public=False,
        headline="Your listing has been removed",
        detail="It is not public, and we are not holding it for re-publication.",
        tone="blocked",
    ),
}


def status(practitioner) -> Status:
    return STATUS_COPY[practitioner.status]


@dataclass(frozen=True)
class Check:
    """One required verification check, as the practitioner needs to see it."""

    type: str
    label: str
    state: str
    state_label: str
    expires_at: object | None
    days_left: int | None
    #: Whether the practitioner needs to do something about this one.
    needs_action: bool
    action: str = ""
    #: Whether this check is one the BADGE depends on. DBS and ICO are not:
    #: DBS gates the scope of a listing (under-18 groups) and ICO is required by
    #: policy but is not in `verification.BASE_REQUIRED`. Showing them in one
    #: undifferentiated list is what made the panel's own copy wrong.
    affects_badge: bool = True

    @property
    def is_expiring_soon(self) -> bool:
        return self.days_left is not None and 0 < self.days_left <= max(REMINDER_DAYS)

    @property
    def has_expired(self) -> bool:
        return self.days_left is not None and self.days_left <= 0

    @property
    def reminder_bucket(self) -> int | None:
        """The tightest reminder threshold this check has crossed, or ``None``.

        Used for the banner's urgency, and named after the same buckets the
        nightly email uses so a practitioner is not told two different things by
        two different channels on the same day.
        """
        if self.days_left is None:
            return None
        for threshold in sorted(REMINDER_DAYS):
            if self.days_left <= threshold:
                return threshold
        return None


#: What a practitioner should do about a check that is not verified. Keyed on the
#: check's own status, because "submitted" and "rejected" need opposite advice.
_CHECK_ACTION = {
    VerificationStatus.NOT_SUBMITTED: "Upload it",
    VerificationStatus.SUBMITTED: "",  # with us; nothing for them to do
    VerificationStatus.IN_REVIEW: "",
    VerificationStatus.REJECTED: "Upload a replacement",
    VerificationStatus.EXPIRED: "Upload the current one",
}


def checks(practitioner) -> list[Check]:
    """Every check this listing needs, in a fixed order, with its own expiry.

    Includes DBS whenever the practitioner has an under-18 client group — it is not
    in ``BASE_REQUIRED``, because it gates the *scope* of a listing rather than the
    badge, but a practitioner who needs one has to see it here or they will not know
    it is outstanding.
    """
    existing = {c.type: c for c in practitioner.verifications.all()}
    labels = dict(VerificationType.choices)
    now = timezone.now()

    wanted = list(verification.required_types(practitioner))
    if practitioner.minor_work_status != MinorWorkStatus.NOT_APPLICABLE:
        wanted.append(VerificationType.DBS)
    # ICO is required of everyone by docs/verification-policy.md but is not in
    # BASE_REQUIRED, so it is shown when a row exists rather than invented here —
    # see the open note in the dashboard's own docs entry.
    if VerificationType.ICO in existing:
        wanted.append(VerificationType.ICO)

    out = []
    for check_type in dict.fromkeys(wanted):
        check = existing.get(check_type)
        state = check.status if check else VerificationStatus.NOT_SUBMITTED
        expires_at = check.expires_at if check else None

        days_left = None
        if expires_at is not None:
            days_left = (expires_at - now) // timedelta(days=1)
            days_left = int(days_left)

        action = _CHECK_ACTION.get(state, "")
        out.append(
            Check(
                type=check_type,
                label=labels.get(check_type, check_type),
                state=state,
                state_label=dict(VerificationStatus.choices).get(state, state),
                expires_at=expires_at,
                days_left=days_left,
                needs_action=bool(action),
                action=action,
                affects_badge=check_type in verification.required_types(practitioner),
            )
        )
    return out


@dataclass(frozen=True)
class MinorWork:
    """The under-18 position, and the countdown when there is one."""

    state: str
    headline: str
    detail: str
    days_left: int | None = None
    tone: str = "neutral"


def minor_work(practitioner) -> MinorWork | None:
    """``None`` when the practitioner has selected no under-18 client groups.

    The PROVISIONAL wording is the brief's, near-verbatim, and it is the sentence
    that matters most on this page: somebody is live and earning while a
    safeguarding check is outstanding, so it has to say exactly which parts of
    their listing are showing and which are not.
    """
    state = practitioner.minor_work_status
    if state == MinorWorkStatus.NOT_APPLICABLE:
        return None

    if state == MinorWorkStatus.CLEARED:
        return MinorWork(
            state=state,
            headline="Your under-18 client groups are showing",
            detail="Your enhanced DBS check is verified and in date.",
            tone="good",
        )

    if state == MinorWorkStatus.PROVISIONAL:
        days = None
        if practitioner.provisional_expires_at:
            days = max(0, (practitioner.provisional_expires_at - timezone.now()).days)
        return MinorWork(
            state=state,
            headline="Your listing is live for adult work",
            detail=(
                "Your under-18 client groups will appear once your DBS is verified. "
                "Everything else on your listing is showing normally."
            ),
            days_left=days,
            tone="warning",
        )

    return MinorWork(
        state=state,
        headline="Your under-18 client groups are not showing",
        detail=(
            "We do not have a verified enhanced DBS check for you, so the client "
            "groups involving under-18s are hidden from your listing and from "
            "search. The rest of your listing is unaffected. Upload a current "
            "enhanced DBS certificate and we will check it."
        ),
        tone="blocked",
    )


def build(practitioner) -> dict:
    """The overview page's own context.

    Deliberately does NOT include ``practitioner`` — ``dashboard.views._context``
    supplies that, and the whole chrome, for every page. Returning it here too
    made ``_context(practitioner, **build(practitioner))`` a duplicate keyword
    argument and a 500 on the dashboard's landing page.
    """
    return {
        "status": status(practitioner),
        "completeness": completeness.report(practitioner),
        "checks": checks(practitioner),
        "minor_work": minor_work(practitioner),
        "latest_review": practitioner.review_requests.exclude(reviewer_notes="")
        .order_by("-submitted_at")
        .first(),
        "open_review": practitioner.review_requests.filter(outcome="").order_by("-submitted_at").first(),
    }
