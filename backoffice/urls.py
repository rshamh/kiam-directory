"""Back-office URLs.

Mounted at ``/backoffice/`` and disallowed in robots.txt. Nothing here is
public: every view carries an ``accounts.access`` predicate, and the Phase 0
two-factor middleware means an unverified staff session cannot reach any of it.

``invite/accept/<token>/`` is the one route an unauthenticated person reaches,
and only by holding a token an admin issued. It is not a signup route — there is
no path from it to an account without a valid invite.
"""

from django.urls import path

from . import views

app_name = "backoffice"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    # Invites
    path("invites/", views.invite_list, name="invite_list"),
    path("invites/accept/<str:token>/", views.invite_accept, name="invite_accept"),
    # Review
    path("review/", views.review_queue, name="review_queue"),
    path("review/<uuid:pk>/", views.review_detail, name="review_detail"),
    # Practitioners
    path("practitioners/", views.practitioner_list, name="practitioner_list"),
    path("practitioners/<uuid:pk>/", views.practitioner_detail, name="practitioner_detail"),
    path("practitioners/<uuid:pk>/suspend/", views.suspend, name="suspend"),
    path("practitioners/<uuid:pk>/lift-suspension/", views.lift_suspension, name="lift_suspension"),
    # Verification workbench
    path(
        "practitioners/<uuid:pk>/verification/",
        views.verification_workbench,
        name="verification_workbench",
    ),
    path(
        "practitioners/<uuid:pk>/verification/<str:check_type>/",
        views.verification_set_status,
        name="verification_set_status",
    ),
    path(
        "practitioners/<uuid:pk>/provisional-dbs/",
        views.provisional_grant,
        name="provisional_grant",
    ),
    path(
        "practitioners/<uuid:pk>/provisional-dbs/extend/",
        views.provisional_extend,
        name="provisional_extend",
    ),
    # Evidence — POST to mint a logged, short-lived link; GET to stream it.
    path("evidence/<uuid:document_id>/open/", views.evidence_open, name="evidence_open"),
    path("evidence/stream/<str:token>/", views.evidence_stream, name="evidence_stream"),
    # Concerns
    path("concerns/", views.concern_queue, name="concern_queue"),
    path("concerns/<uuid:pk>/", views.concern_detail, name="concern_detail"),
    # Audit
    path("audit/", views.audit_log, name="audit_log"),
]
