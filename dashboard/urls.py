"""Dashboard URLs.

Mounted at ``/dashboard/``, disallowed in robots.txt, and every view carries the
``can_use_dashboard`` + ``owns_practitioner`` pair through ``practitioner_view``.

``KIAM_UI["AUTH"]["DASHBOARD_URL"]`` has pointed at ``dashboard:home`` since
Phase 0 — the chrome has been rendering a link to this namespace, and until now
the name did not resolve.

Every state-changing route is POST-only. The two that end a listing
(``unpublish``, ``remove``) additionally take a GET for the confirmation page, so
the destructive act is never something a link can do.
"""

from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.home, name="home"),
    path("send-to-us/", views.submit, name="submit"),
    # Editors
    path("profile/", views.profile, name="profile"),
    path("where-you-work/", views.locations, name="locations"),
    path("where-you-work/add/", views.location_edit, name="location_add"),
    path("where-you-work/<uuid:pk>/", views.location_edit, name="location_edit"),
    path("where-you-work/<uuid:pk>/remove/", views.location_delete, name="location_delete"),
    path("what-you-do/", views.taxonomy, name="taxonomy"),
    path("credentials/", views.credentials, name="credentials"),
    path("availability/", views.availability, name="availability"),
    # Evidence
    path("documents/", views.documents, name="documents"),
    path("documents/<uuid:pk>/delete/", views.document_delete, name="document_delete"),
    # Insights
    path("insights/", views.insights_view, name="insights"),
    # Account and security
    path("security/", views.security, name="security"),
    path("security/email/cancel/", views.cancel_email_change, name="cancel_email_change"),
    path("security/sessions/revoke/", views.revoke_session, name="revoke_session"),
    path("security/sessions/revoke-others/", views.revoke_other_sessions, name="revoke_other_sessions"),
    path("security/two-factor/off/", views.disable_two_factor, name="disable_two_factor"),
    # Taking the listing down
    path("take-my-listing-down/", views.unpublish, name="unpublish"),
    path("remove-me/", views.remove, name="remove"),
]
