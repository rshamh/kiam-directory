"""What the listing did, from ``DailyMetric``.

The brief: *"This is what will justify the subscription in year two, so make it
genuinely useful rather than decorative."* Three things follow from taking that
seriously.

**A number with nothing to compare it to is decoration.** "41 profile views" tells
a practitioner nothing they can act on. So every total comes with the previous
equal-length period beside it and the change between them, and the series is
returned per-day so the page can draw the shape rather than assert a number.

**The funnel is the useful part.** Impressions → views → contacts is the only chain
on this page that answers a question a practitioner actually has: *am I not being
found, or am I being found and passed over?* Those two problems have completely
different fixes — specialities and location versus photo, intro and fees — and the
conversion rates are what tell them apart. `funnel()` names both diagnoses.

**Honesty about what the numbers are not.** ``directory.services.metrics`` filters
crawlers on a crude user-agent substring match and counts anything that lies. It
holds no visitor-level data at all, by design, so there is no session to
de-duplicate: two views from the same person on the same day are two views. Both
facts are surfaced in the UI rather than left for somebody to infer when the
numbers do not match their own website's analytics. `CAVEATS` is that copy.

**Aggregate counters only.** This module reads ``DailyMetric``, which has no IP, no
session and no user agent — that is what keeps it outside the cookie-consent burden
and out of scope for a subject access request. There is nothing here to join to a
person and nothing to add.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.db.models import Sum
from django.utils import timezone

#: The window the dashboard shows by default. Thirty days is long enough to survive
#: a quiet week and short enough that a change a practitioner made last month shows
#: up in it.
WINDOW_DAYS = 30

#: The five counters, in the order they make sense to read: how many people saw
#: you, how many looked, how many got in touch.
SERIES = (
    ("search_impressions", "Appeared in search"),
    ("profile_views", "Profile views"),
    ("email_reveals", "Email revealed"),
    ("phone_reveals", "Phone revealed"),
    ("website_clicks", "Website clicks"),
)

#: Which counters count as "somebody tried to contact me".
CONTACT_FIELDS = ("email_reveals", "phone_reveals", "website_clicks")

CAVEATS = (
    "These are counts of page views and clicks, not of people. We hold no "
    "visitor-level data at all — no cookies, no sessions, no IP addresses — so if "
    "the same person looks at your listing twice in a day that is two views.",
    "We filter out the obvious search-engine crawlers, but the filter is deliberately "
    "simple, so treat small numbers as approximate.",
)


@dataclass(frozen=True)
class Total:
    """One counter over a window, against the window before it."""

    field: str
    label: str
    value: int
    previous: int

    @property
    def change(self) -> int:
        return self.value - self.previous

    @property
    def change_percent(self) -> int | None:
        """``None`` when the previous window was zero.

        Not "100%" and not "∞": going from 0 to 3 is not a percentage increase, and
        printing one is the kind of flattering nonsense that makes a whole page
        untrustworthy.
        """
        if not self.previous:
            return None
        return round((self.change / self.previous) * 100)

    @property
    def direction(self) -> str:
        if self.change > 0:
            return "up"
        if self.change < 0:
            return "down"
        return "flat"


@dataclass(frozen=True)
class Point:
    day: date
    values: dict[str, int]


@dataclass(frozen=True)
class Funnel:
    """Impressions → views → contacts, and what a bad rate at each step means."""

    impressions: int
    views: int
    contacts: int

    @property
    def view_rate(self) -> int | None:
        if not self.impressions:
            return None
        return round((self.views / self.impressions) * 100)

    @property
    def contact_rate(self) -> int | None:
        if not self.views:
            return None
        return round((self.contacts / self.views) * 100)

    @property
    def diagnosis(self) -> str:
        """The one sentence worth reading on this page.

        Deliberately not a recommendation about clinical practice — it is about the
        listing. Kiam does not advise practitioners on how to practise; it can tell
        them their fee is missing.
        """
        if not self.impressions:
            return (
                "Your listing has not appeared in search results yet. That usually "
                "means the specialities and location on it are not matching what "
                "people are searching for."
            )
        if self.view_rate is not None and self.view_rate < 5:
            return (
                "People are finding your listing in search but not opening it. A "
                "photo, a clearer first line and a fee usually make the difference — "
                "those are what show on the search result itself."
            )
        if self.views and not self.contacts:
            return (
                "People are reading your listing but not getting in touch. It is "
                "worth re-reading your introduction as though you were the client, "
                "and checking your fees and availability are up to date."
            )
        return "People are finding your listing, opening it, and getting in touch."


@dataclass(frozen=True)
class Insights:
    window_days: int
    start: date
    end: date
    totals: tuple[Total, ...]
    points: tuple[Point, ...]
    funnel: Funnel
    #: Yesterday, for the "so far" line. `None` when there is no row for it.
    latest: Point | None
    has_any_data: bool

    def total(self, field: str) -> Total | None:
        return next((t for t in self.totals if t.field == field), None)

    @property
    def peak(self) -> int:
        """The largest single-day value across every series, for the chart's scale.

        One shared scale, not one per series: the point of the chart is that
        impressions are an order of magnitude above contacts, and per-series scaling
        would hide exactly that.
        """
        return max((max(p.values.values(), default=0) for p in self.points), default=0)


def _sum(queryset) -> dict[str, int]:
    aggregate = queryset.aggregate(**{field: Sum(field) for field, _ in SERIES})
    return {field: aggregate.get(field) or 0 for field, _ in SERIES}


def build(practitioner, *, window_days: int = WINDOW_DAYS, today: date | None = None) -> Insights:
    """Totals, the day-by-day series, and the funnel.

    ``today`` is injectable so a test can assert the window boundaries without
    freezing the clock for everything else in the process.
    """
    today = today or timezone.localdate()
    start = today - timedelta(days=window_days - 1)
    previous_start = start - timedelta(days=window_days)

    rows = practitioner.metrics.filter(date__gte=previous_start, date__lte=today)
    by_day = {row.date: row for row in rows}

    current = _sum(rows.filter(date__gte=start))
    previous = _sum(rows.filter(date__lt=start))

    totals = tuple(
        Total(field=field, label=label, value=current[field], previous=previous[field])
        for field, label in SERIES
    )

    points = tuple(
        Point(
            day=start + timedelta(days=offset),
            values={
                field: getattr(by_day.get(start + timedelta(days=offset)), field, 0) or 0
                for field, _ in SERIES
            },
        )
        for offset in range(window_days)
    )

    funnel = Funnel(
        impressions=current["search_impressions"],
        views=current["profile_views"],
        contacts=sum(current[field] for field in CONTACT_FIELDS),
    )

    return Insights(
        window_days=window_days,
        start=start,
        end=today,
        totals=totals,
        points=points,
        funnel=funnel,
        latest=points[-1] if points else None,
        has_any_data=any(current.values()) or any(previous.values()),
    )


def snapshot(practitioner, *, today: date | None = None) -> dict:
    """The three numbers the overview page shows, without building the series."""
    full = build(practitioner, today=today)
    return {
        "views": full.total("profile_views"),
        "impressions": full.total("search_impressions"),
        "contacts": Total(
            field="contacts",
            label="Contact details revealed",
            value=full.funnel.contacts,
            previous=sum(
                (t.previous for t in full.totals if t.field in CONTACT_FIELDS),
                0,
            ),
        ),
        "window_days": full.window_days,
        "has_any_data": full.has_any_data,
    }
