"""
Directory models.

Three decisions carried through the whole schema:

1. A practitioner has MANY practice locations. Search matches ANY of them. This is the
   one modelling mistake that is genuinely expensive to retrofit.
2. Online delivery is independent of location — a practitioner can serve UK-wide with
   no physical address at all.
3. Publication is a state machine, and verification state is COMPUTED. Neither is a
   boolean anyone sets by hand. See directory/services/verification.py.

Requires: PostGIS, pg_trgm.
"""

import uuid

from django.conf import settings
from django.contrib.gis.db import models as gis
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone

from .storages import private_storage


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# Taxonomy — four independent axes. See directory/taxonomy.py for the seed data
# and docs/architecture.md for why these are not one list.
# ---------------------------------------------------------------------------


class Profession(UUIDModel):
    """What the practitioner IS. Restricted titles need a matching verified registration."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)  # clinical content — Dr. Abbass sign-off
    restricted = models.BooleanField(
        default=False,
        help_text="Protected or constrained title. Cannot publish without a VERIFIED "
        "registration with one of required_bodies.",
    )
    required_bodies = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class SpecialityCategory(UUIDModel):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)  # clinical content — sign-off required
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "speciality categories"

    def __str__(self):
        return self.name


class Speciality(UUIDModel):
    """What the practitioner TREATS."""

    category = models.ForeignKey(SpecialityCategory, on_delete=models.PROTECT, related_name="specialities")
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=160)
    synonyms = ArrayField(
        models.CharField(max_length=80),
        default=list,
        blank=True,
        help_text="Feeds the search vector. Never displayed publicly.",
    )
    description = models.TextField(blank=True)
    implies_minors = models.BooleanField(
        default=False, help_text="Implies work with under-18s. Contributes to the DBS requirement."
    )
    seo_enabled = models.BooleanField(
        default=True, help_text="Eligible for /[speciality]/[town] landing pages."
    )
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category__sort_order", "sort_order", "name"]
        verbose_name_plural = "specialities"

    def __str__(self):
        return self.name


class Approach(UUIDModel):
    """HOW the practitioner works. Never names a prescription-only medicine."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    abbreviation = models.CharField(max_length=16, blank=True)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class ClientGroup(UUIDModel):
    """WHO the practitioner sees. is_minors drives the enhanced DBS requirement."""

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    min_age = models.PositiveSmallIntegerField(null=True, blank=True)
    max_age = models.PositiveSmallIntegerField(null=True, blank=True)
    is_minors = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Language(UUIDModel):
    code = models.CharField(max_length=8, unique=True)  # ISO 639-1, or "bsl"
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class FundingOption(UUIDModel):
    class Group(models.TextChoices):
        SELF_PAY = "self_pay", "Self-pay"
        INSURER = "insurer", "Insurer"
        NHS = "nhs", "NHS"
        EMPLOYER = "employer", "Employer"

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    group = models.CharField(max_length=16, choices=Group.choices)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["group", "sort_order", "name"]

    def __str__(self):
        return self.name


class SessionFormat(UUIDModel):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Practitioner
# ---------------------------------------------------------------------------


class PublicationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    IN_REVIEW = "in_review", "In review"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published"
    SUSPENDED = "suspended", "Suspended"
    UNPUBLISHED = "unpublished", "Unpublished (consent withdrawn)"
    REMOVED = "removed", "Removed"


class MinorWorkStatus(models.TextChoices):
    """
    Whether under-18 client groups may be shown publicly and matched in search.

    PROVISIONAL means the listing is live for ADULT WORK ONLY while a DBS is pending.
    It is not a softer version of CLEARED — it gates the scope of the listing, not the
    rigour of the check.
    """

    NOT_APPLICABLE = "n/a", "No under-18 groups selected"
    PROVISIONAL = "provisional", "Provisional — adult work only, DBS pending"
    CLEARED = "cleared", "Cleared — enhanced DBS verified"
    BLOCKED = "blocked", "Blocked — window elapsed, rejected or expired"


class DeliveryMode(models.TextChoices):
    IN_PERSON = "in_person", "In person"
    ONLINE = "online", "Online"
    BOTH = "both", "Both"


class Gender(models.TextChoices):
    FEMALE = "female", "Female"
    MALE = "male", "Male"
    NON_BINARY = "non_binary", "Non-binary"
    UNDISCLOSED = "undisclosed", "Prefer not to say"


class WaitTime(models.TextChoices):
    IMMEDIATE = "immediate", "Within a week"
    SHORT = "short", "1–2 weeks"
    MEDIUM = "medium", "2–4 weeks"
    LONG = "long", "4+ weeks"
    CLOSED = "closed", "Not accepting"


class Practitioner(UUIDModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="practitioner",
    )

    # --- Public identity (intake form section A)
    slug = models.SlugField(max_length=140, unique=True)  # immutable once published
    full_name = models.CharField(max_length=160)
    display_title = models.CharField(max_length=20, blank=True)
    post_nominals = models.CharField(max_length=160, blank=True)
    pronouns = models.CharField(max_length=40, blank=True)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)
    headshot = models.ImageField(upload_to="headshots/", blank=True)  # PUBLIC storage
    profession = models.ForeignKey(
        Profession, on_delete=models.PROTECT, null=True, related_name="practitioners"
    )
    years_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    qualified_since = models.PositiveSmallIntegerField(null=True, blank=True)

    # --- Narrative (sections E & F). Linted for POM names and efficacy claims on submit.
    intro = models.TextField(blank=True, help_text="~100–150 words")
    services = models.TextField(blank=True)

    # --- Delivery (section C)
    delivery_mode = models.CharField(max_length=16, choices=DeliveryMode.choices, default=DeliveryMode.BOTH)
    offers_online = models.BooleanField(default=True)
    online_coverage = models.CharField(max_length=120, blank=True)
    is_prescriber = models.BooleanField(default=False)
    offers_supervision = models.BooleanField(default=False)

    # --- Published contact (section B). At least one required — enforced at review.
    public_email = models.EmailField(blank=True)
    public_phone = models.CharField(max_length=40, blank=True)
    public_website = models.URLField(blank=True)
    booking_url = models.URLField(blank=True)

    # --- Availability summary (booking itself is a later phase, and needs legal review)
    accepting_new_clients = models.BooleanField(default=True)
    typical_wait = models.CharField(max_length=16, choices=WaitTime.choices, blank=True)
    evening_appointments = models.BooleanField(default=False)
    weekend_appointments = models.BooleanField(default=False)
    availability_note = models.CharField(max_length=200, blank=True)

    # --- Fees (pence)
    fee_min = models.PositiveIntegerField(null=True, blank=True)
    fee_max = models.PositiveIntegerField(null=True, blank=True)
    free_initial_call = models.BooleanField(default=False)
    offers_sliding_scale = models.BooleanField(default=False)

    # --- Taxonomy
    specialities = models.ManyToManyField(Speciality, blank=True, related_name="practitioners")
    approaches = models.ManyToManyField(Approach, blank=True, related_name="practitioners")
    client_groups = models.ManyToManyField(ClientGroup, blank=True, related_name="practitioners")
    languages = models.ManyToManyField(Language, blank=True, related_name="practitioners")
    funding_options = models.ManyToManyField(FundingOption, blank=True, related_name="practitioners")
    session_formats = models.ManyToManyField(SessionFormat, blank=True, related_name="practitioners")

    # --- State
    status = models.CharField(
        max_length=20, choices=PublicationStatus.choices, default=PublicationStatus.DRAFT
    )
    published_at = models.DateTimeField(null=True, blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suspensions_made",
    )
    suspend_reason = models.TextField(blank=True)
    last_review_at = models.DateTimeField(null=True, blank=True)

    # --- Ranking inputs (denormalised, recomputed on write)
    completeness = models.PositiveSmallIntegerField(default=0)  # 0–100
    last_active_at = models.DateTimeField(null=True, blank=True)

    # --- COMPUTED verification state.
    # Written only by directory.services.verification.recompute(). No admin toggle
    # exists for these and none may be added — see CLAUDE.md.
    is_verified = models.BooleanField(default=False)
    credentials_checked_at = models.DateTimeField(
        null=True, blank=True, help_text='Date shown on the badge: "Credentials checked 12 July 2026".'
    )
    verification_expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Earliest expiry across required checks. Badge lapses here."
    )
    minor_work_status = models.CharField(
        max_length=16, choices=MinorWorkStatus.choices, default=MinorWorkStatus.NOT_APPLICABLE
    )
    provisional_expires_at = models.DateTimeField(null=True, blank=True)
    provisional_extensions = models.PositiveSmallIntegerField(
        default=0, help_text="A second extension requires Dr. Abbass sign-off."
    )

    # --- Later phases. Present so nothing needs migrating; unused at launch.
    plan_tier = models.CharField(max_length=16, default="free")
    featured_until = models.DateTimeField(null=True, blank=True)

    search_vector = SearchVectorField(null=True, editable=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "is_verified"]),
            models.Index(fields=["status", "accepting_new_clients"]),
            models.Index(fields=["status", "minor_work_status"]),
            models.Index(fields=["verification_expires_at"]),
            models.Index(fields=["provisional_expires_at"]),
            models.Index(fields=["featured_until"]),
            GinIndex(fields=["search_vector"]),
        ]

    def __str__(self):
        return self.full_name

    @property
    def is_featured(self):
        return bool(self.featured_until and self.featured_until > timezone.now())

    @property
    def can_show_minor_groups(self):
        """
        PROVISIONAL deliberately returns False. Call this in the profile serialiser;
        the search queryset applies the equivalent filter. Both are required — either
        one alone leaks a provisional practitioner into under-18 results.
        """
        return self.minor_work_status == MinorWorkStatus.CLEARED

    def visible_client_groups(self):
        qs = self.client_groups.all()
        if not self.can_show_minor_groups:
            qs = qs.filter(is_minors=False)
        return qs


class PractitionerLocation(UUIDModel):
    """
    A practitioner may practise from several addresses. Search matches ANY of them.
    Do not collapse this to a single address on the Practitioner.
    """

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="locations")
    label = models.CharField(max_length=80, blank=True)
    address_line1 = models.CharField(max_length=160, blank=True)
    address_line2 = models.CharField(max_length=160, blank=True)
    city = models.CharField(max_length=80)
    county = models.CharField(max_length=80, blank=True)
    postcode = models.CharField(max_length=12, blank=True)
    country_code = models.CharField(max_length=2, default="GB")

    geo = gis.PointField(geography=True, srid=4326, spatial_index=True)

    is_primary = models.BooleanField(default=False)
    is_public = models.BooleanField(
        default=True, help_text="False = used for the search radius only; the address is not displayed."
    )
    days_at_site = models.CharField(max_length=80, blank=True)  # "Tue, Thu"

    # Accessibility — not on the intake form; added.
    step_free_access = models.BooleanField(default=False)
    wheelchair_access = models.BooleanField(default=False)
    parking_available = models.BooleanField(default=False)
    hearing_loop = models.BooleanField(default=False)
    near_public_transport = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_primary", "city"]
        indexes = [models.Index(fields=["city"]), models.Index(fields=["postcode"])]

    def __str__(self):
        return f"{self.practitioner.full_name} — {self.city}"


# ---------------------------------------------------------------------------
# Credentials (section G)
# ---------------------------------------------------------------------------


class Qualification(UUIDModel):
    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="qualifications")
    title = models.CharField(max_length=200)
    institution = models.CharField(max_length=200)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    verified = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "-year"]


class Registration(UUIDModel):
    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="registrations")
    body = models.CharField(max_length=40)  # GMC, HCPC, BACP, UKCP, BABCP, NMC, BPS
    registration_no = models.CharField(max_length=40)
    status = models.CharField(max_length=60, blank=True)
    register_url = models.URLField(
        blank=True, help_text="Public register entry — used for the quarterly re-check."
    )
    verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["body", "registration_no"]),
            models.Index(fields=["last_checked_at"]),
        ]


# ---------------------------------------------------------------------------
# Verification — private, never published
# ---------------------------------------------------------------------------


class VerificationType(models.TextChoices):
    IDENTITY = "identity", "Photo ID"
    REGISTRATION = "registration", "Regulator / professional body"
    QUALIFICATION = "qualification", "Qualification"
    INSURANCE = "insurance", "Professional indemnity insurance"
    DBS = "dbs", "Enhanced DBS"
    PRESCRIBER = "prescriber", "Prescriber status"
    ICO = "ico", "ICO registration"


class VerificationStatus(models.TextChoices):
    NOT_SUBMITTED = "not_submitted", "Not submitted"
    SUBMITTED = "submitted", "Submitted"
    IN_REVIEW = "in_review", "In review"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class VerificationCheck(UUIDModel):
    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="verifications")
    type = models.CharField(max_length=20, choices=VerificationType.choices)
    status = models.CharField(
        max_length=20, choices=VerificationStatus.choices, default=VerificationStatus.NOT_SUBMITTED
    )
    checked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="checks_made"
    )
    checked_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(
        null=True, blank=True, help_text="Insurance and DBS expire. The badge lapses automatically with them."
    )
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    # DBS only — the provisional window that lets a listing go live for adult work.
    provisional_granted_at = models.DateTimeField(null=True, blank=True)
    provisional_until = models.DateTimeField(null=True, blank=True)
    provisional_granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="provisionals_granted",
    )

    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["practitioner", "type"], name="unique_check_per_type")]
        indexes = [models.Index(fields=["status", "expires_at"])]


def evidence_path(instance, filename):
    return f"evidence/{instance.practitioner_id}/{uuid.uuid4()}/{filename}"


class Document(UUIDModel):
    """
    Evidence files. PRIVATE storage, signed URLs only, every access logged.
    Never served from the same storage backend as headshots.
    """

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="documents")
    # Authored as `check`, which Django refuses outright:
    #
    #   directory.Document: (models.E020) The 'Document.check()' class method is
    #   currently overridden by <ForwardManyToOneDescriptor>
    #
    # `Model.check()` is the classmethod the system-check framework calls on every
    # model, so a field of that name shadows it and the app will not load at all —
    # no migrations, no shell, no tests. Renamed to the least surprising thing that
    # still says what it points at.
    #
    # `related_name` is unchanged, so the reverse accessor stays `check.documents`;
    # only the forward one moves, `document.check` -> `document.verification_check`.
    # Nothing references it yet (the workbench is Phase 2), so the cost of doing
    # this now is zero and it only grows.
    verification_check = models.ForeignKey(
        VerificationCheck, on_delete=models.SET_NULL, null=True, blank=True, related_name="documents"
    )
    type = models.CharField(max_length=20, choices=VerificationType.choices)
    # `storage=None` (as authored, with a `# settings.PRIVATE_STORAGE` placeholder)
    # falls back to django.core.files.storage.default_storage — the PUBLIC backend.
    # Every passport scan and DBS certificate would have landed in the headshot
    # bucket with a working public URL, silently, while this class's own docstring
    # said the opposite. Filled in with the callable the placeholder pointed at.
    #
    # A callable, not `storages["private"]` evaluated at import: Django resolves it
    # per access, so dev (filesystem), test (temp dir) and prod (a separate S3
    # bucket) each get their own backend without the choice being frozen into a
    # migration. private_storage() also refuses to return anything that is not a
    # guarded backend — see directory/storages.py.
    file = models.FileField(upload_to=evidence_path, storage=private_storage)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    delete_after = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Retention after listing removal. Period to be confirmed by the solicitor.",
    )

    class Meta:
        indexes = [models.Index(fields=["delete_after"])]


class DocumentAccessLog(UUIDModel):
    """Required for the DPIA. Written by directory.services.documents.open_evidence()."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="access_log")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    accessed_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------------------
# Consent (section I) — versioned and withdrawable
# ---------------------------------------------------------------------------


class ConsentRecord(UUIDModel):
    """
    Lawful basis for publishing a practitioner's personal data is consent, so it must be
    as easy to withdraw as to give. The dashboard has a one-click unpublish; the intake
    form's "contact Kiam Clinic to be removed" wording needs updating to match.
    """

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="consents")
    terms_version = models.CharField(max_length=40)  # "listing-agreement-v1.0"

    consent_publish = models.BooleanField()
    consent_registered = models.BooleanField()
    consent_independent = models.BooleanField()
    consent_accurate = models.BooleanField()
    consent_removal_path = models.BooleanField()

    signed_name = models.CharField(max_length=160)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)


# ---------------------------------------------------------------------------
# Review workflow, audit, reports
# ---------------------------------------------------------------------------


class ReviewOutcome(models.TextChoices):
    APPROVED = "approved", "Approved"
    CHANGES_REQUESTED = "changes_requested", "Changes requested"
    REJECTED = "rejected", "Rejected"


class ReviewRequest(UUIDModel):
    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="review_requests")
    snapshot = models.JSONField(help_text="What was submitted, so reviewers see exactly what they approved.")
    changed_fields = ArrayField(models.CharField(max_length=60), default=list, blank=True)
    lint_flags = models.JSONField(
        default=dict, blank=True, help_text="POM names, efficacy claims, restricted titles, child-work terms."
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviews_done",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.CharField(max_length=20, choices=ReviewOutcome.choices, blank=True)
    reviewer_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["submitted_at"]
        indexes = [models.Index(fields=["outcome", "submitted_at"])]


class AuditLog(UUIDModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=80)  # "practitioner.publish", "verification.dbs.provisional_granted"
    entity_type = models.CharField(max_length=60)
    entity_id = models.CharField(max_length=64)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["entity_type", "entity_id"]),
            models.Index(fields=["created_at"]),
        ]


class ConcernReport(UUIDModel):
    """
    "Report a concern about this listing." NOT a clinical complaints channel — clinical
    complaints go to the practitioner's regulator, and the public page must say so.
    """

    class Category(models.TextChoices):
        INACCURATE = "inaccurate", "Details are inaccurate"
        NOT_REGISTERED = "not_registered", "Registration concern"
        MISLEADING = "misleading", "Misleading claims"
        OTHER = "other", "Other"

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="reports")
    category = models.CharField(max_length=20, choices=Category.choices)
    detail = models.TextField()
    reporter_email = models.EmailField(blank=True)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    handled_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["practitioner", "handled_at"])]


class SlugRedirect(UUIDModel):
    """Preserves inbound links and accrued SEO authority when a slug changes."""

    old_slug = models.SlugField(max_length=140, unique=True)
    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="slug_redirects")
    created_at = models.DateTimeField(auto_now_add=True)


class TaxonomyRequest(UUIDModel):
    """Free-text "other" submissions, triaged by admin into the controlled vocabulary."""

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="taxonomy_requests")
    axis = models.CharField(max_length=20)  # speciality | approach | language | funding
    proposed_term = models.CharField(max_length=120)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


# ---------------------------------------------------------------------------
# Analytics — aggregated daily counters, never raw visitor rows
# ---------------------------------------------------------------------------


class DailyMetric(UUIDModel):
    """
    What justifies the subscription in year two. Start collecting from day one.
    No per-visitor rows: this is counters only, which keeps it outside the cookie
    consent burden and out of scope for a DSAR.
    """

    practitioner = models.ForeignKey(Practitioner, on_delete=models.CASCADE, related_name="metrics")
    date = models.DateField()
    search_impressions = models.PositiveIntegerField(default=0)
    profile_views = models.PositiveIntegerField(default=0)
    email_reveals = models.PositiveIntegerField(default=0)
    phone_reveals = models.PositiveIntegerField(default=0)
    website_clicks = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["practitioner", "date"], name="unique_metric_per_day")]
        indexes = [models.Index(fields=["date"])]
