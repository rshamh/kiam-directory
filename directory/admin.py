"""Staff-facing Django admin.

**The hard rule.** These five fields are computed by
``directory.services.verification.recompute()`` and by nothing else:

    is_verified  credentials_checked_at  verification_expires_at
    minor_work_status  provisional_expires_at

They are read-only here, absent from every ``list_editable``, and excluded from
every ModelForm. There is no admin toggle and none may be added — the moment a
human can set the badge, someone sets it as a favour and it stops meaning
anything (CLAUDE.md). ``minor_work_status`` is worse than that: a hand-set
``CLEARED`` puts a practitioner with no DBS in front of somebody looking for a
therapist for their child.

``directory/tests/test_admin_readonly.py`` asserts this for every registered
admin, by introspection rather than by listing them — so a new ModelAdmin, or a
field added to an existing one, fails the suite rather than shipping.

The way to change verification state is to record evidence: edit the
``VerificationCheck``, then recompute. The action below does exactly that and is
the only supported route.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from .models import (
    Approach,
    AuditLog,
    ClientGroup,
    ConcernReport,
    ConsentRecord,
    DailyMetric,
    Document,
    DocumentAccessLog,
    FundingOption,
    Language,
    Practitioner,
    PractitionerLocation,
    Profession,
    Qualification,
    Registration,
    ReviewRequest,
    SessionFormat,
    SlugRedirect,
    Speciality,
    SpecialityCategory,
    TaxonomyRequest,
    VerificationCheck,
)
from .services import verification

#: Written only by directory.services.verification.recompute(). Read by the admin
#: classes below AND by the test that enforces this — one list, one definition.
COMPUTED_VERIFICATION_FIELDS = (
    "is_verified",
    "credentials_checked_at",
    "verification_expires_at",
    "minor_work_status",
    "provisional_expires_at",
)


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


class TaxonomyAdmin(admin.ModelAdmin):
    """Shared shape for the vocabulary models.

    `active` is editable in the list because deactivating a term is the supported
    way to retire it — seed_taxonomy never deletes, so this is the switch.
    """

    list_display = ("name", "slug", "sort_order", "active")
    list_editable = ("sort_order", "active")
    list_filter = ("active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("sort_order", "name")


@admin.register(Profession)
class ProfessionAdmin(TaxonomyAdmin):
    list_display = ("name", "slug", "restricted", "sort_order", "active")
    list_filter = ("active", "restricted")


@admin.register(SpecialityCategory)
class SpecialityCategoryAdmin(TaxonomyAdmin):
    pass


@admin.register(Speciality)
class SpecialityAdmin(TaxonomyAdmin):
    list_display = ("name", "category", "slug", "implies_minors", "seo_enabled", "sort_order", "active")
    list_filter = ("active", "category", "implies_minors", "seo_enabled")
    list_select_related = ("category",)


@admin.register(Approach)
class ApproachAdmin(TaxonomyAdmin):
    pass


@admin.register(ClientGroup)
class ClientGroupAdmin(TaxonomyAdmin):
    list_display = ("name", "slug", "is_minors", "min_age", "max_age", "sort_order", "active")
    list_filter = ("active", "is_minors")


@admin.register(SessionFormat)
class SessionFormatAdmin(TaxonomyAdmin):
    pass


@admin.register(FundingOption)
class FundingOptionAdmin(TaxonomyAdmin):
    list_display = ("name", "slug", "group", "sort_order", "active")
    list_filter = ("active", "group")


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "sort_order", "active")
    list_editable = ("sort_order", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")


# ---------------------------------------------------------------------------
# Practitioner
# ---------------------------------------------------------------------------


class PractitionerLocationInline(admin.TabularInline):
    """A practitioner has MANY locations; search matches any of them."""

    model = PractitionerLocation
    extra = 0
    fields = ("label", "city", "postcode", "geo", "is_primary", "is_public", "days_at_site")


class RegistrationInline(admin.TabularInline):
    model = Registration
    extra = 0
    fields = ("body", "registration_no", "status", "register_url", "verified", "last_checked_at")


class QualificationInline(admin.TabularInline):
    model = Qualification
    extra = 0


class VerificationCheckInline(admin.TabularInline):
    """The supported way to change verification state: record the evidence.

    Editing a check here and then running the "Recompute" action is the whole
    workflow. Nothing sets the badge directly.
    """

    model = VerificationCheck
    extra = 0
    fields = ("type", "status", "checked_at", "expires_at", "provisional_until", "notes")
    readonly_fields = ("provisional_granted_at", "provisional_granted_by")


@admin.register(Practitioner)
class PractitionerAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "profession",
        "status",
        "verified_badge",
        "minor_work_status",
        "accepting_new_clients",
        "completeness",
    )
    list_filter = ("status", "is_verified", "minor_work_status", "accepting_new_clients", "profession")
    search_fields = ("full_name", "slug", "public_email")
    list_select_related = ("profession",)
    prepopulated_fields = {"slug": ("full_name",)}
    filter_horizontal = (
        "specialities",
        "approaches",
        "client_groups",
        "languages",
        "funding_options",
        "session_formats",
    )
    inlines = [
        PractitionerLocationInline,
        RegistrationInline,
        QualificationInline,
        VerificationCheckInline,
    ]
    actions = ["recompute_verification"]

    # The five computed fields, plus the denormalised and audit columns nothing
    # should hand-edit either.
    readonly_fields = (
        *COMPUTED_VERIFICATION_FIELDS,
        "provisional_extensions",
        "search_vector",
        "created_at",
        "updated_at",
        "published_at",
        "suspended_at",
        "suspended_by",
    )

    fieldsets = (
        (
            None,
            {"fields": ("user", "slug", "full_name", "display_title", "post_nominals", "pronouns", "gender")},
        ),
        (
            "Profession",
            {
                "fields": (
                    "profession",
                    "years_experience",
                    "qualified_since",
                    "is_prescriber",
                    "offers_supervision",
                )
            },
        ),
        ("Narrative", {"fields": ("intro", "services")}),
        ("Delivery", {"fields": ("delivery_mode", "offers_online", "online_coverage")}),
        ("Contact", {"fields": ("public_email", "public_phone", "public_website", "booking_url")}),
        (
            "Availability",
            {
                "fields": (
                    "accepting_new_clients",
                    "typical_wait",
                    "evening_appointments",
                    "weekend_appointments",
                    "availability_note",
                )
            },
        ),
        ("Fees", {"fields": ("fee_min", "fee_max", "free_initial_call", "offers_sliding_scale")}),
        (
            "Taxonomy",
            {
                "fields": (
                    "specialities",
                    "approaches",
                    "client_groups",
                    "languages",
                    "funding_options",
                    "session_formats",
                )
            },
        ),
        (
            "Publication",
            {"fields": ("status", "published_at", "suspended_at", "suspended_by", "suspend_reason")},
        ),
        (
            "Verification (computed — read only)",
            {
                "description": (
                    "Written only by directory.services.verification.recompute(), from dated "
                    "VerificationCheck rows. To change any of these, record the evidence in the "
                    "Verification checks below and run the “Recompute verification” action. "
                    "There is no toggle and none may be added."
                ),
                "fields": (*COMPUTED_VERIFICATION_FIELDS, "provisional_extensions"),
            },
        ),
        (
            "System",
            {
                "classes": ("collapse",),
                "fields": (
                    "completeness",
                    "last_active_at",
                    "plan_tier",
                    "featured_until",
                    "search_vector",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    @admin.display(description="Badge", boolean=True, ordering="is_verified")
    def verified_badge(self, obj):
        return obj.is_verified

    @admin.action(description="Recompute verification from evidence")
    def recompute_verification(self, request, queryset):
        """Re-derive state from the checks on file.

        Not a way to *set* anything — it reruns the same function the nightly
        sweep runs. Useful after editing a check inline.
        """
        for practitioner in queryset.prefetch_related("verifications", "client_groups"):
            verification.recompute(practitioner)
        self.message_user(
            request,
            f"Recomputed verification for {queryset.count()} practitioner(s) from evidence on file.",
            messages.SUCCESS,
        )


@admin.register(PractitionerLocation)
class PractitionerLocationAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "city", "postcode", "is_primary", "is_public")
    list_filter = ("is_primary", "is_public", "city", "step_free_access")
    search_fields = ("practitioner__full_name", "city", "postcode")
    list_select_related = ("practitioner",)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@admin.register(VerificationCheck)
class VerificationCheckAdmin(admin.ModelAdmin):
    """Evidence, not state. Editing here then recomputing is the workflow."""

    list_display = ("practitioner", "type", "status", "checked_at", "expires_at", "provisional_until")
    list_filter = ("type", "status")
    search_fields = ("practitioner__full_name",)
    list_select_related = ("practitioner",)
    readonly_fields = ("provisional_granted_at", "provisional_granted_by", "created_at", "updated_at")

    def save_model(self, request, obj, form, change):
        """Recompute immediately, so the admin cannot leave state stale.

        Without this, editing a check and navigating away would leave the badge
        showing yesterday's answer until the nightly sweep.
        """
        super().save_model(request, obj, form, change)
        verification.recompute(obj.practitioner)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """Evidence files. Read-only, and the file itself is not linked.

    Private-storage objects have no public URL by construction
    (directory/storages.py). The verifier workbench with signed, logged access is
    Phase 2; until then this is a metadata view only, so nothing here can hand
    out a passport scan.
    """

    list_display = ("practitioner", "type", "original_filename", "uploaded_at", "delete_after")
    list_filter = ("type",)
    search_fields = ("practitioner__full_name", "original_filename")
    list_select_related = ("practitioner",)
    fields = (
        "practitioner",
        "verification_check",
        "type",
        "original_filename",
        "mime_type",
        "size_bytes",
        "sha256",
        "uploaded_at",
        "delete_after",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DocumentAccessLog)
class DocumentAccessLogAdmin(admin.ModelAdmin):
    """Required for the DPIA. Append-only by definition."""

    list_display = ("document", "user", "ip", "accessed_at")
    list_filter = ("accessed_at",)
    readonly_fields = ("document", "user", "ip", "accessed_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Workflow and audit
# ---------------------------------------------------------------------------


@admin.register(ReviewRequest)
class ReviewRequestAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "submitted_at", "outcome", "reviewed_by", "reviewed_at")
    list_filter = ("outcome",)
    search_fields = ("practitioner__full_name",)
    list_select_related = ("practitioner", "reviewed_by")
    readonly_fields = ("snapshot", "changed_fields", "lint_flags", "submitted_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Append-only. An audit log an admin can edit is not an audit log."""

    list_display = ("created_at", "action", "entity_type", "entity_id", "actor")
    list_filter = ("action", "entity_type")
    search_fields = ("entity_id", "action")
    list_select_related = ("actor",)
    readonly_fields = ("actor", "action", "entity_type", "entity_id", "before", "after", "ip", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    """The lawful basis for publishing someone's data. Never editable."""

    list_display = ("practitioner", "terms_version", "granted_at", "withdrawn_at")
    list_filter = ("terms_version",)
    search_fields = ("practitioner__full_name", "signed_name")
    list_select_related = ("practitioner",)
    readonly_fields = tuple(f.name for f in ConsentRecord._meta.fields if f.name != "id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ConcernReport)
class ConcernReportAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "category", "created_at", "handled_by", "handled_at")
    list_filter = ("category", "handled_at")
    search_fields = ("practitioner__full_name", "detail")
    list_select_related = ("practitioner", "handled_by")
    readonly_fields = ("practitioner", "category", "detail", "reporter_email", "created_at")

    def has_delete_permission(self, request, obj=None):
        """Concern reports are evidence that Kiam monitors its listings.

        A deletable complaints queue is not a monitoring record — and the one
        someone would reach for the delete button on is exactly the one that
        matters.
        """
        return False


@admin.register(TaxonomyRequest)
class TaxonomyRequestAdmin(admin.ModelAdmin):
    list_display = ("proposed_term", "axis", "practitioner", "created_at", "resolved_at")
    list_filter = ("axis", "resolved_at")
    search_fields = ("proposed_term", "practitioner__full_name")
    list_select_related = ("practitioner",)


@admin.register(SlugRedirect)
class SlugRedirectAdmin(admin.ModelAdmin):
    list_display = ("old_slug", "practitioner", "created_at")
    search_fields = ("old_slug", "practitioner__full_name")
    list_select_related = ("practitioner",)


@admin.register(DailyMetric)
class DailyMetricAdmin(admin.ModelAdmin):
    """Aggregated counters, never per-visitor rows."""

    list_display = ("practitioner", "date", "search_impressions", "profile_views", "email_reveals")
    list_filter = ("date",)
    search_fields = ("practitioner__full_name",)
    list_select_related = ("practitioner",)
    readonly_fields = tuple(f.name for f in DailyMetric._meta.fields if f.name != "id")

    def has_add_permission(self, request):
        return False


@admin.register(Registration)
class RegistrationAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "body", "registration_no", "verified", "last_checked_at", "expires_at")
    list_filter = ("body", "verified")
    search_fields = ("practitioner__full_name", "registration_no")
    list_select_related = ("practitioner",)


@admin.register(Qualification)
class QualificationAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "title", "institution", "year", "verified")
    list_filter = ("verified",)
    search_fields = ("practitioner__full_name", "title", "institution")
    list_select_related = ("practitioner",)

    @admin.display(description="Institution")
    def institution_display(self, obj):
        return format_html("<span>{}</span>", obj.institution)
