"""Auth URLs.

There is deliberately **no signup route**. Account creation is by admin ``Invite``
only (Phase 2). A test asserts that no URL in the project reverses to one — see
``accounts/tests/test_no_signup.py``.
"""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("login/check-your-email/", views.login_sent_view, name="login_sent"),
    # The token is in the path rather than a query string so it stays out of
    # `document.referrer` and out of any third-party analytics that reads it.
    path("login/<str:token>/", views.magic_link_consume_view, name="magic_link_consume"),
    path("logout/", views.logout_view, name="logout"),
    # Set / change / remove a password. Needs a session — which is also the
    # recovery route: forgotten it, sign in with a magic link, land here.
    path("password/", views.set_password_view, name="set_password"),
    path("two-factor/set-up/", views.two_factor_setup_view, name="two_factor_setup"),
    path("two-factor/set-up/qr.svg", views.two_factor_qr_view, name="two_factor_qr"),
    path("two-factor/", views.two_factor_verify_view, name="two_factor_verify"),
]
