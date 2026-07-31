"""The admin's own add-a-user form.

Between Phase 0 and this file, **no account could be created through the Django
admin at all**. `AdminUserCreationForm` declares `usable_password`, `password1`
and `password2`; declared fields are required whether or not a fieldset mentions
them, and `add_fieldsets` mentioned none of them. So the page rendered without
the inputs and every POST failed on "This field is required" for two fields that
were not on it — an error a human cannot act on, about a control they cannot see.

The suite could not see it either: nothing exercised the add view. Everything
here goes through the real HTTP surface for that reason.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.admin.models import LogEntry

from apps.accounts.models import User

pytestmark = pytest.mark.django_db

ADMIN = f"/{settings.ADMIN_URL_PATH.strip('/')}"
ADD_URL = f"{ADMIN}/accounts/user/add/"


@pytest.fixture
def staff_console(client, superadmin):
    """A superadmin session that has passed the TOTP challenge.

    Without the device the two-factor middleware redirects every admin URL to the
    challenge, so a test that skipped this would pass against a completely broken
    form by never reaching it.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice

    device = TOTPDevice.objects.create(user=superadmin, confirmed=True, name="default")
    client.force_login(superadmin)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()
    return client


def test_every_required_field_on_the_add_form_is_rendered(staff_console):
    """The general guard, and the one that would have caught the original bug.

    Asserting "password1 is on the page" pins today's symptom. Asserting that
    nothing the form insists on is missing from the page pins the *shape* of the
    mistake, which is what recurs: a declared field on a form Django supplies,
    invisible because a fieldset did not list it.
    """
    from django.contrib import admin as django_admin
    from django.contrib.admin.utils import flatten_fieldsets

    model_admin = django_admin.site._registry[User]
    rendered = set(flatten_fieldsets(model_admin.add_fieldsets))

    response = staff_console.get(ADD_URL)
    assert response.status_code == 200
    form = response.context["adminform"].form

    required = {name for name, field in form.fields.items() if field.required}
    missing = required - rendered
    assert not missing, f"required but not on the page: {sorted(missing)}"


def test_an_account_is_created_with_no_password_by_default(staff_console):
    """The documented state: magic link in, no password until its owner sets one."""
    response = staff_console.post(
        ADD_URL,
        {
            "email": "newverifier@example.com",
            "display_name": "New Verifier",
            "role": User.Role.VERIFIER,
            "is_active": "on",
            "usable_password": "false",
        },
    )

    assert response.status_code == 302, getattr(response, "context", {}) and dict(
        response.context["adminform"].form.errors
    )
    user = User.objects.get(email="newverifier@example.com")
    assert user.role == User.Role.VERIFIER
    assert user.has_usable_password() is False


def test_the_password_radio_starts_disabled(staff_console):
    """Django's default is Enabled. Here the default has to be the project's."""
    form = staff_console.get(ADD_URL).context["adminform"].form
    assert form.fields["usable_password"].initial == "false"


def test_an_admin_may_set_a_password_when_somebody_asks(staff_console):
    response = staff_console.post(
        ADD_URL,
        {
            "email": "withpassword@example.com",
            "display_name": "With Password",
            "role": User.Role.ADMIN,
            "is_active": "on",
            "usable_password": "true",
            "password1": "hedgerow-lantern-9214",
            "password2": "hedgerow-lantern-9214",
        },
    )

    assert response.status_code == 302
    user = User.objects.get(email="withpassword@example.com")
    assert user.has_usable_password() is True
    assert user.check_password("hedgerow-lantern-9214") is True


def test_a_case_different_address_is_refused_as_a_duplicate(staff_console):
    """`email` is unique but case-sensitive, and `normalize_email` lowercases only
    the domain — so two rows differing in the local part satisfy the constraint
    and break BOTH accounts.

    `CaseInsensitiveEmailBackend` catches `MultipleObjectsReturned` and fails
    closed, locking each of them out of password sign-in; `magic_link` uses
    `.first()` and would mail whichever row the database happened to return.
    """
    User.objects.create_user(email="nadia@example.com", role=User.Role.PRACTITIONER)

    response = staff_console.post(
        ADD_URL,
        {
            "email": "Nadia@Example.com",
            "display_name": "Nadia",
            "role": User.Role.PRACTITIONER,
            "is_active": "on",
            "usable_password": "false",
        },
    )

    assert response.status_code == 200
    assert "email" in response.context["adminform"].form.errors
    assert User.objects.filter(email__iexact="nadia@example.com").count() == 1


def test_setting_a_password_from_the_admin_is_logged(staff_console, superadmin):
    """Not a second, unaudited way in — Django's own change-password view writes a
    LogEntry naming the admin who did it."""
    target = User.objects.create_user(email="locked-out@example.com", role=User.Role.PRACTITIONER)
    before = LogEntry.objects.count()

    response = staff_console.post(
        f"{ADMIN}/accounts/user/{target.pk}/password/",
        {
            "usable_password": "true",
            "password1": "thistledown-anchor-44",
            "password2": "thistledown-anchor-44",
        },
    )

    assert response.status_code == 302
    target.refresh_from_db()
    assert target.check_password("thistledown-anchor-44") is True
    assert LogEntry.objects.count() == before + 1
    assert LogEntry.objects.latest("id").user_id == superadmin.pk


def test_the_change_page_never_exposes_a_hash(staff_console, superadmin):
    """`password` is back on the change form, and it is Django's
    ReadOnlyPasswordHashField — algorithm and a button, never anything replayable."""
    superadmin.set_password("something-real-for-the-widget")
    superadmin.save()

    body = staff_console.get(f"{ADMIN}/accounts/user/{superadmin.pk}/change/").content.decode()

    assert superadmin.password not in body
    assert superadmin.password.split("$")[-1] not in body
