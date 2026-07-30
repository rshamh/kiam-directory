"""
Search.

Location-prioritised: a practitioner matches if ANY of their practice locations falls
within the radius, OR they offer online sessions and the user allows online.

Ranking is two-tier — Featured (paid, labelled, capped) then a weighted score. Within
score bands results are shuffled with a per-session seed, so the same few practitioners
don't occupy the top slots permanently. The seed is constant for the session, so
pagination stays stable.

Requires PostGIS and pg_trgm.

---

**Authored elsewhere and adopted at Phase 4** (CLAUDE.md: "review, don't rewrite" —
recovered from the rooms repo's history, `git -C ../rooms show f49d0c2^:search.py`).
The ranking, the weights, the banded shuffle and the under-18 gate are as authored.
Six things had to change, and each is commented where it happens rather than
absorbed silently:

1. **Location predicates are ANDed onto ONE relation, not filtered separately.**
   Two `.filter()` calls on `locations` can match two DIFFERENT locations, so
   "step-free" + "within 10 miles" was satisfiable by a practitioner whose Epsom
   office is in range and whose Guildford office is step-free.
2. **`in_person_only` did not filter anything** unless a location was also given.
3. **The accessibility facets were one field**, not the three the sidebar offers.
4. **`FEATURED_CAP_PER_PAGE` was defined and never used.** The cap is implemented
   in `results_page()`.
5. **Speciality categories** are filterable, so the two-level tree works as a
   plain form without JavaScript.
6. **The text query FILTERS, not only ranks.** As authored, `q` annotated a rank
   and nothing else, so a search for "zzzznonsense" returned the entire
   directory and the empty state was unreachable from the search box.
7. **`homepage_grid()` seeded its daily shuffle from the UTC date**, so for half
   the year it rotated at 01:00 local while the result shuffle rotated at
   midnight. Phase 5, when the grid stopped being unused code.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import Case, CharField, F, FloatField, Min, Q, Value, When
from django.db.models.functions import MD5, Cast, Coalesce, Concat, Floor, Greatest, Least
from django.utils import timezone

from directory.models import MinorWorkStatus, Practitioner, PublicationStatus

DEFAULT_RADIUS_MILES = 10
RADIUS_OPTIONS = [1, 3, 5, 10, 15, 25, 50]
SCORE_BAND = 0.05
FEATURED_CAP_PER_PAGE = 3

# Weights. Keep them here, summing to 1.0, so tuning is one edit and one test.
W_PROXIMITY = 0.35
W_TEXT = 0.25
W_COMPLETENESS = 0.15
W_VERIFIED = 0.10
W_ACCEPTING = 0.10
W_RECENT = 0.05

#: Location attributes a visitor can require. ADOPTION CHANGE (3): the authored
#: file had `step_free_access` alone; the sidebar offers three, and they have to be
#: satisfied by the SAME address as the radius — see `_location_filter`.
ACCESS_FACETS = ("step_free_access", "parking_available", "hearing_loop")


@dataclass
class SearchParams:
    q: str = ""
    lat: float | None = None
    lng: float | None = None
    radius_miles: int = DEFAULT_RADIUS_MILES
    include_online: bool = True

    specialities: list[str] = field(default_factory=list)
    #: ADOPTION CHANGE (5). Selecting a category matches every speciality in it,
    #: so the two-level tree is a real form control rather than a JavaScript
    #: convenience that vanishes without it.
    speciality_categories: list[str] = field(default_factory=list)
    approaches: list[str] = field(default_factory=list)
    client_groups: list[str] = field(default_factory=list)
    professions: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    funding: list[str] = field(default_factory=list)
    session_formats: list[str] = field(default_factory=list)
    genders: list[str] = field(default_factory=list)

    in_person_only: bool = False
    online_only: bool = False
    accepting_new_clients: bool = False
    max_wait: str = ""
    evening: bool = False
    weekend: bool = False
    verified_only: bool = False
    is_prescriber: bool = False
    fee_max_pence: int | None = None
    min_years_experience: int | None = None
    step_free_access: bool = False
    parking_available: bool = False
    hearing_loop: bool = False

    seed: int = 0  # stable per session
    page: int = 1
    page_size: int = 20

    @property
    def has_location(self) -> bool:
        return self.lat is not None and self.lng is not None

    @property
    def wants_physical_venue(self) -> bool:
        """Whether the visitor has asked for something only an address can provide.

        Any accessibility requirement implies attending in person: "step-free
        access" is meaningless about a video call. So it also turns off the
        "…or they work online" escape hatch below, because including online
        listings there would answer a question about a building with a listing
        that has none.
        """
        return self.in_person_only or any(getattr(self, name) for name in ACCESS_FACETS)


WAIT_ORDER = ["immediate", "short", "medium", "long"]


def _location_filter(p: SearchParams, point):
    """Every constraint on a practice address, as ONE Q against ONE relation.

    ADOPTION CHANGE (1), and the reason it matters: Django resolves each
    `.filter()` call on a multi-valued relation against its own join. Two calls
    therefore ask "is ANY location in range?" and, separately, "is ANY location
    step-free?" — which a practitioner with an in-range office and a different
    step-free office passes, while having nowhere that is both. Someone who needs
    step-free access would be sent to the wrong address.

    ANDing the predicates inside a single Q pins them to the same joined row.
    """
    location_q = Q()

    if point is not None:
        location_q &= Q(locations__geo__distance_lte=(point, D(mi=p.radius_miles)))
    for name in ACCESS_FACETS:
        if getattr(p, name):
            location_q &= Q(**{f"locations__{name}": True})

    return location_q


def build_queryset(p: SearchParams):
    qs = Practitioner.objects.filter(status=PublicationStatus.PUBLISHED)

    # --- facets ------------------------------------------------------------
    if p.specialities or p.speciality_categories:
        # ADOPTION CHANGE (5): one OR, not two filters — a category and a
        # speciality inside it must widen the result set, not intersect to
        # nothing.
        speciality_q = Q()
        if p.specialities:
            speciality_q |= Q(specialities__slug__in=p.specialities)
        if p.speciality_categories:
            speciality_q |= Q(specialities__category__slug__in=p.speciality_categories)
        qs = qs.filter(speciality_q)
    if p.approaches:
        qs = qs.filter(approaches__slug__in=p.approaches)
    if p.funding:
        qs = qs.filter(funding_options__slug__in=p.funding)
    if p.session_formats:
        qs = qs.filter(session_formats__slug__in=p.session_formats)
    if p.languages:
        qs = qs.filter(languages__code__in=p.languages)
    if p.professions:
        qs = qs.filter(profession__slug__in=p.professions)
    if p.genders:
        qs = qs.filter(gender__in=p.genders)

    if p.client_groups:
        qs = qs.filter(client_groups__slug__in=p.client_groups)

    # UNDER-18 GATE. If the user filters to children or adolescents, only
    # practitioners with a CLEARED enhanced DBS may match. PROVISIONAL listings are
    # live for adult work and must never surface here.
    #
    # This mirrors Practitioner.can_show_minor_groups used by the profile
    # serialiser. BOTH are required — either one alone leaks.
    #
    # It is applied OUTSIDE the `if p.client_groups` block because a request can
    # ask about under-18 work without naming a client group at all — see
    # `_requests_minor_work`.
    if _requests_minor_work(p):
        qs = qs.filter(minor_work_status=MinorWorkStatus.CLEARED)

    if p.accepting_new_clients:
        qs = qs.filter(accepting_new_clients=True)
    if p.verified_only:
        qs = qs.filter(is_verified=True)
    if p.is_prescriber:
        qs = qs.filter(is_prescriber=True)
    if p.evening:
        qs = qs.filter(evening_appointments=True)
    if p.weekend:
        qs = qs.filter(weekend_appointments=True)
    if p.online_only:
        qs = qs.filter(offers_online=True)
    if p.fee_max_pence:
        qs = qs.filter(Q(fee_min__isnull=True) | Q(fee_min__lte=p.fee_max_pence))
    if p.min_years_experience:
        qs = qs.filter(years_experience__gte=p.min_years_experience)
    if p.max_wait and p.max_wait in WAIT_ORDER:
        qs = qs.filter(typical_wait__in=WAIT_ORDER[: WAIT_ORDER.index(p.max_wait) + 1])

    # --- location gate -----------------------------------------------------
    point = None
    if p.has_location:
        point = Point(p.lng, p.lat, srid=4326)

    location_q = _location_filter(p, point)

    if location_q:
        if p.wants_physical_venue or not p.include_online:
            qs = qs.filter(location_q)
        else:
            qs = qs.filter(location_q | Q(offers_online=True))
    elif p.in_person_only:
        # ADOPTION CHANGE (2). `in_person_only` used to be read only inside the
        # location branch, so selecting "in person" without naming a place
        # filtered nothing at all and quietly returned online-only listings.
        qs = qs.filter(locations__isnull=False)

    return qs.distinct(), point


def _includes_minor_group(slugs) -> bool:
    from directory.models import ClientGroup

    return ClientGroup.objects.filter(slug__in=slugs, is_minors=True).exists()


def _requests_minor_work(p: SearchParams) -> bool:
    """Whether this search is asking about work with under-18s.

    Two ways, and the second was added at the Phase 4 compliance review.

    **Client groups** — the axis `docs/content-compliance.md` §4 names, and the one
    `recompute()` derives the DBS requirement from.

    **An explicitly selected `implies_minors` speciality** — "Child & adolescent
    ADHD assessment", "Autism assessment (children)". Without this clause a
    PROVISIONAL practitioner, whose minor client groups BOTH gates correctly hide,
    was still returned for `?speciality=child-adhd-assessment` with that speciality
    printed on their card. Both gates fired, suppressed the label, and search handed
    over the person anyway in answer to an explicitly child-focused request. The
    ClientGroup/Speciality distinction is internal; a parent ticking a box labelled
    "Child & adolescent ADHD assessment" cannot see it.

    **This is a fail-safe interim, not the decision.** Whether a speciality tag
    creates a DBS requirement is Dr. Abbass's and the CQC lead's call — the proper
    fix is in `recompute()` so the profile, the search filter and the requirement
    itself move together, and it needs `search_index` to stop putting a hidden
    speciality in band B or `?q=child adhd` keeps matching one. Until that is
    recorded, search declines the risky side of an open safeguarding question.

    Two known limits, both deliberate:

    * **Categories are not gated.** "Neurodevelopmental" *contains* a child
      speciality, so gating the category would hide every adult ADHD practitioner
      without a DBS. Too blunt to be right.
    * **Free text is not gated.** Matching `q` against child speciality names
      sounds appealing until you notice that "adhd" is a lexeme in "Child &
      adolescent ADHD assessment", so a plain search for "adhd" would hide every
      non-cleared practitioner. That residual belongs to the open decision above.

    The taxonomy asymmetry that makes this Dr. Abbass's call rather than ours:
    `child-adhd-assessment` is unambiguously child work, but `separation-anxiety`
    is also `implies_minors=True` and occurs in adults — so this clause
    over-restricts on that term. Over-restricting is the safe direction, and that
    is the only reason it is acceptable to ship.
    """
    from directory.models import Speciality

    if p.client_groups and _includes_minor_group(p.client_groups):
        return True

    return bool(
        p.specialities and Speciality.objects.filter(slug__in=p.specialities, implies_minors=True).exists()
    )


def search(p: SearchParams):
    qs, point = build_queryset(p)
    now = timezone.now()

    # --- proximity ---------------------------------------------------------
    if point is not None:
        qs = qs.annotate(distance=Min(Distance("locations__geo", point)))
        radius_m = p.radius_miles * 1609.344
        # 1 at the doorstep, 0 at the radius edge. Online-only listings (no distance)
        # sit at 0.5 so a strong national match isn't buried under a weak local one.
        proximity = Case(
            When(distance__isnull=True, then=Value(0.5)),
            default=Greatest(Value(0.0), Value(1.0) - Cast(F("distance"), FloatField()) / Value(radius_m)),
            output_field=FloatField(),
        )
    else:
        qs = qs.annotate(distance=Value(None, output_field=FloatField()))
        proximity = Value(0.5, output_field=FloatField())

    # --- text relevance ----------------------------------------------------
    if p.q.strip():
        query = SearchQuery(p.q, config="english")
        # ADOPTION CHANGE (6): the query FILTERS as well as ranks.
        #
        # As authored, `q` only annotated a rank, so a search for "zzzznonsense"
        # returned every published practitioner in the directory — the search box
        # did not search. Two things follow from that, and both are worse than the
        # thing it was avoiding:
        #
        #   * A typo answers with the whole directory, presented as though it
        #     matched. Somebody looking for "EMDR" is shown forty people, most of
        #     whom do not offer it, with no signal which is which.
        #   * The empty state becomes unreachable from the search box, so the
        #     "try a wider radius or drop a filter" copy this phase requires can
        #     never appear for a text query.
        #
        # Ranking still does the work of ORDERING the matches; this only decides
        # membership. A practitioner whose search_vector is NULL cannot match text,
        # which is correct — and the two signals plus the nightly
        # `rebuild_search_index` are what keep the vector from being NULL.
        qs = qs.filter(search_vector=query)
        qs = qs.annotate(rank=SearchRank(F("search_vector"), query))
        text = Least(Value(1.0), Coalesce(Cast(F("rank"), FloatField()), Value(0.0)) * Value(4.0))
    else:
        text = Value(0.0, output_field=FloatField())

    qs = qs.annotate(
        proximity_score=proximity,
        text_score=text,
        score=(
            Value(W_PROXIMITY) * proximity
            + Value(W_TEXT) * text
            + Value(W_COMPLETENESS) * (Cast(F("completeness"), FloatField()) / Value(100.0))
            + Value(W_VERIFIED)
            * Case(When(is_verified=True, then=Value(1.0)), default=Value(0.0), output_field=FloatField())
            + Value(W_ACCEPTING)
            * Case(
                When(accepting_new_clients=True, then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
            + Value(W_RECENT)
            * Case(
                When(last_active_at__gte=now - timedelta(days=90), then=Value(1.0)),
                default=Value(0.0),
                output_field=FloatField(),
            )
        ),
    ).annotate(
        featured=Case(
            When(featured_until__gt=now, then=Value(1)), default=Value(0), output_field=FloatField()
        ),
        band=Floor(F("score") / Value(SCORE_BAND)),
        shuffle=MD5(Concat(Cast("id", output_field=CharField()), Value(str(p.seed)))),
    )

    return (
        qs.order_by("-featured", "-band", "shuffle")
        .select_related("profession")
        .prefetch_related("locations")
    )


# ===========================================================================
# Phase 4 additions
# ===========================================================================
# The featured cap and pagination. FEATURED_CAP_PER_PAGE existed as a constant
# with no consumer (ADOPTION CHANGE 4), which would have shipped a paid tier able
# to take a whole page the moment anyone bought one.


#: How many speciality pills a result card carries. Enough to recognise somebody,
#: few enough that twenty cards are still scannable — a result list where every
#: card lists eleven specialities is a wall of text, which is what golden rule #3
#: rules out.
TOP_SPECIALITIES_ON_CARD = 3

#: Metres in a mile. The one place it is written down.
METRES_PER_MILE = 1609.344


def _miles(value):
    """The annotated distance in miles, or ``None``.

    ``Min(Distance(...))`` over a geography column comes back as metres, but
    whether Django hands that over as a ``Distance`` measure object or a bare
    float depends on how the aggregate resolves its output field — so this accepts
    either rather than betting on one and breaking on an upgrade.

    ``None`` is meaningful and must survive: a practitioner matched because they
    work online has no address and no distance, and showing them "0.0 miles away"
    would be a lie about a person somebody might travel to.
    """
    if value is None:
        return None
    if hasattr(value, "mi"):
        return value.mi
    try:
        return float(value) / METRES_PER_MILE
    except (TypeError, ValueError):
        return None


def _decorate(items, *, top_specialities: int, radius_miles: int | None = None) -> None:
    """Attach what the result card needs, in one query for the whole page.

    Done here rather than in the template because a card that reaches for
    `practitioner.specialities.all|slice` runs a query per result — twenty extra
    round trips on a page with a latency budget (docs/seo.md).

    ``distance_miles`` is dropped when it falls OUTSIDE the requested radius, and
    that is not cosmetic. A practitioner can match a five-mile search two ways:
    they have an address in range, or they work online and the visitor allowed
    online. The second kind still has a nearest address, and printing "14.8 miles
    away" on a five-mile search reads as a broken filter rather than as "this
    person will see you over video". The card falls back to "Online", which is why
    they are in the list.
    """
    for item in items:
        miles = _miles(getattr(item, "distance", None))
        if miles is not None and radius_miles is not None and miles > radius_miles:
            miles = None
        item.distance_miles = miles

    if not items:
        return

    from directory.models import Speciality

    by_practitioner: dict = {}
    rows = (
        Speciality.objects.filter(practitioners__in=[i.pk for i in items])
        .values_list("practitioners__id", "name")
        .order_by("category__sort_order", "sort_order", "name")
    )
    for practitioner_id, name in rows:
        bucket = by_practitioner.setdefault(practitioner_id, [])
        if len(bucket) < top_specialities:
            bucket.append({"name": name})

    for item in items:
        item.top_specialities = by_practitioner.get(item.pk, [])


def decorate_cards(items, *, radius_miles: int | None = None) -> None:
    """Attach what ``_practitioner_card.html`` needs to an arbitrary list of rows.

    A public seam onto ``_decorate``, added at Phase 5 because the home-page grid
    renders the same card component from a different queryset and had no way to ask
    for the same decoration without reaching into a private function. The cap on
    speciality pills is applied here rather than passed in, so the card carries the
    same number of pills wherever it appears.
    """
    _decorate(items, top_specialities=TOP_SPECIALITIES_ON_CARD, radius_miles=radius_miles)


def wider_radius(current: int) -> int | None:
    """The next radius up, for the empty state's "search further out" link."""
    larger = [option for option in RADIUS_OPTIONS if option > current]
    return larger[0] if larger else None


@dataclass
class ResultPage:
    """One page of results, with the featured block already capped."""

    items: list
    page: int
    page_size: int
    total: int
    featured_total: int

    @property
    def num_pages(self) -> int:
        if self.page_size <= 0:
            return 1
        return max(1, -(-self.total // self.page_size))

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.num_pages

    @property
    def start_index(self) -> int:
        return 0 if not self.total else (self.page - 1) * self.page_size + 1

    @property
    def end_index(self) -> int:
        return (self.page - 1) * self.page_size + len(self.items)


def results_page(p: SearchParams) -> ResultPage:
    """Assemble one page: at most ``FEATURED_CAP_PER_PAGE`` featured, then the rest.

    Split into two querysets rather than sliced from one, because "at most three
    per page" is not something an ORDER BY can express — a single ordered query
    with six featured listings puts six at the top of page one, which is what the
    cap exists to prevent. Paid placement is capped and labelled or it is a
    doorway (docs/content-compliance.md §6).

    Nothing is featured yet, and this degenerates cleanly to ordinary pagination
    when nothing is: ``featured_total`` is 0, the reserved slots are 0, and every
    slot on the page goes to the standard tier. It is built now so the cap and the
    label exist before the first person pays for one.
    """
    now = timezone.now()
    ranked = search(p)

    is_featured = Q(featured_until__gt=now)
    featured_qs = ranked.filter(is_featured)
    standard_qs = ranked.filter(~is_featured)

    page_size = max(1, p.page_size)
    cap = min(FEATURED_CAP_PER_PAGE, page_size)

    featured_total = featured_qs.count()
    standard_total = standard_qs.count()
    total = featured_total + standard_total

    # Clamp to the last real page. `?page=9999` used to return an empty result set
    # under the "No practitioners match these filters" copy — a soft 404 whose text
    # was also untrue, because the filters matched fine and it was the page that did
    # not exist. Clamping shows the last page instead, which is what somebody who
    # over-typed a page number wanted.
    last_page = max(1, -(-total // page_size))
    page = min(max(1, p.page), last_page)

    # How many featured slots earlier pages already used. Not `(page-1)*cap`
    # blindly: once the featured listings run out, later pages reserve nothing and
    # the standard offset has to account for that or results are skipped.
    featured_consumed = min(featured_total, (page - 1) * cap)
    featured_here = max(0, min(cap, featured_total - featured_consumed))

    standard_consumed = max(0, (page - 1) * page_size - featured_consumed)
    standard_here = page_size - featured_here

    items = [
        *featured_qs[featured_consumed : featured_consumed + featured_here],
        *standard_qs[standard_consumed : standard_consumed + standard_here],
    ]
    _decorate(
        items,
        top_specialities=TOP_SPECIALITIES_ON_CARD,
        radius_miles=p.radius_miles if p.has_location else None,
    )

    return ResultPage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        featured_total=featured_total,
    )


def weights() -> dict[str, float]:
    """The ranking weights, for the tuning test and for the debug panel.

    One place to read them from, so a test can assert they still sum to 1.0
    without restating the list and drifting from it.
    """
    return {
        "proximity": W_PROXIMITY,
        "text": W_TEXT,
        "completeness": W_COMPLETENESS,
        "verified": W_VERIFIED,
        "accepting": W_ACCEPTING,
        "recent": W_RECENT,
    }


#: A listing has to be this complete to appear in the home-page grid. The grid is
#: the first twelve practitioners a stranger ever sees, and a listing with no
#: intro, no specialities and no photo represents the directory badly on the one
#: page with the most authority on the subdomain.
HOMEPAGE_MIN_COMPLETENESS = 70


def _ungated_minor_work_ids():
    """Listings that advertise work with under-18s without a cleared DBS.

    A practitioner with an ``implies_minors`` speciality — "Child & adolescent ADHD
    assessment", "Autism assessment (children)" — whose ``minor_work_status`` is
    anything but ``CLEARED``. ``NOT_APPLICABLE`` is in scope and is the whole point:
    it is the state CLAUDE.md's open question 1 produces, where the listing selected
    only adult client groups, so ``recompute()`` derived no DBS requirement at all
    and the badge is full.

    An explicit id subquery rather than a compound ``exclude()``: Django's
    exclude-across-a-multi-valued-relation semantics are subtle enough that the next
    person to read them has to think, and this is a safeguarding control.
    """
    return (
        Practitioner.objects.filter(specialities__implies_minors=True)
        .exclude(minor_work_status=MinorWorkStatus.CLEARED)
        .values("pk")
    )


def homepage_grid(limit: int = 12, *, day=None):
    """
    Rotates daily: cacheable for 24h, but not the same twelve faces forever.
    Featured listings take the first slots and are always labelled as such.

    ``day`` exists so a test can assert the rotation without waiting a day, and so
    two calls inside one request cannot straddle midnight.

    Returns a **list**, not a queryset, because ``FEATURED_CAP_PER_PAGE`` is not
    something an ORDER BY can express — the same reason ``results_page()`` splits
    into two querysets.

    ADOPTION CHANGE (7), Phase 5: the seed was ``timezone.now().date()``, which is
    the UTC date. Between midnight and 01:00 British Summer Time that is
    *yesterday*, so the grid rotated at 01:00 local for half the year while
    ``search.services.params.daily_seed()`` — the equivalent seed for the result
    shuffle — rotated at midnight. Same clock for both now.

    Two things were added at the Phase 5 compliance review, and both are about the
    grid being an EDITORIAL SAMPLE rather than an answer to a question.

    **The featured cap applies here too.** Without it the grid ordered
    ``-featured, shuffle`` and sliced, so four paid listings took the first four of
    twelve slots — on a page whose own disclosure promised no more than three, which
    is a worse disclosure than none. It is also exactly what ADOPTION CHANGE (4)
    fixed for search: a paid tier able to take a whole page the moment anyone buys
    one (docs/content-compliance.md §6).

    **Listings that advertise under-18 work without a cleared DBS are excluded.**
    This is NOT a fourth copy of the under-18 gate, and the distinction matters
    because ``directory/services/profile.py`` records the standing decision that
    adding a speciality gate at one surface would recreate the one-sided leak
    CLAUDE.md warns about. The two real gates — ``can_show_minor_groups`` and the
    search queryset — answer "may this listing be shown to somebody asking about
    children?", and both still do, unchanged. This clause answers a different
    question: "should Kiam *choose* this listing, unprompted, for the twelve faces on
    its own front page?" Nobody asked for it, the pill sits under Kiam's own claim to
    have checked the listing, and excluding it from a sample of twelve out of
    twenty-eight hides nothing — the profile is unchanged and the listing stays
    searchable. There is no asymmetry for a later change to break.

    The underlying defect is still CLAUDE.md open question 1: ``recompute()`` derives
    ``works_with_minors`` from client groups alone, so a speciality that implies
    child work requires no DBS. That is **Dr. Abbass / CQC compliance lead**, and
    Phase 5 raises its priority rather than settling it — this page turned a
    query-triggered risk into published copy.
    """
    day_seed = (day or timezone.localdate()).isoformat()
    now = timezone.now()

    ranked = (
        Practitioner.objects.filter(
            status=PublicationStatus.PUBLISHED, completeness__gte=HOMEPAGE_MIN_COMPLETENESS
        )
        .exclude(pk__in=_ungated_minor_work_ids())
        .annotate(
            featured=Case(
                When(featured_until__gt=now, then=Value(1)), default=Value(0), output_field=FloatField()
            ),
            shuffle=MD5(Concat(Cast("id", output_field=CharField()), Value(day_seed))),
        )
        .select_related("profession")
        .order_by("shuffle")
    )

    is_featured = Q(featured_until__gt=now)
    cap = min(FEATURED_CAP_PER_PAGE, max(0, limit))

    featured = list(ranked.filter(is_featured)[:cap])
    standard = list(ranked.filter(~is_featured)[: limit - len(featured)])

    return featured + standard


# ---------------------------------------------------------------------------
# search_vector maintenance
#
# The authored file carried a worked example here of how to keep the vector
# fresh. It is BUILT — `directory/services/search_index.py`, plus the two signals
# in `directory/signals.py` and the nightly `rebuild_search_index` command — and
# the real implementation had to solve two things the sketch did not: a
# SearchVector over a joined field raises inside a queryset `.update()`, and a
# NULL column NULLs the whole concatenation. The example is replaced by this
# pointer rather than left in place, because two sets of instructions for one job
# is how the wrong one gets followed.
#
# Synonyms are in the B band, which is what makes "add" find "ADHD".
# ---------------------------------------------------------------------------
