"""No verification field is writable through the admin. Anywhere.

This is the test CLAUDE.md asks for, and it is written by **introspection over
the whole admin registry** rather than by naming PractitionerAdmin. That matters:
a test that checks one class passes happily while someone adds the toggle to a
new ModelAdmin, an inline, or a second registration of the same model.

What it walks:

* every registered ``ModelAdmin`` whose model has any of the five fields;
* every ``InlineModelAdmin`` on every one of them;
* the actual generated ``ModelForm`` — which is the only thing that really
  decides what a POST can write, and which catches a field re-enabled through
  ``fields``/``fieldsets`` even when ``readonly_fields`` looks right.

If this fails, do not add the field to ``readonly_fields`` to make it green.
Read ``directory/services/verification.py`` first: the field is computed, and
something is trying to set it by hand.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth.models import AnonymousUser

from directory.admin import COMPUTED_VERIFICATION_FIELDS
from directory.factories import PractitionerFactory
from directory.models import Practitioner

pytestmark = pytest.mark.django_db


def _admins_for_model_with_computed_fields():
    """Every registered admin whose model carries any computed field."""
    found = []
    for model, model_admin in admin.site._registry.items():
        names = {f.name for f in model._meta.get_fields() if hasattr(f, "name")}
        if names & set(COMPUTED_VERIFICATION_FIELDS):
            found.append((model, model_admin))
    return found


def test_the_five_computed_fields_are_the_ones_the_service_writes():
    """Keeps this test honest if the service starts computing something new.

    COMPUTED_VERIFICATION_FIELDS is what the admin locks; this asserts it still
    matches what recompute() actually writes.
    """
    import inspect

    from directory.services import verification

    source = inspect.getsource(verification.recompute)
    written = source.split("update(")[1].split(")")[0]

    for field in COMPUTED_VERIFICATION_FIELDS:
        assert f"{field}=" in written, (
            f"{field} is locked in the admin but recompute() no longer writes it — "
            "one of the two is out of date."
        )


def test_at_least_one_admin_is_actually_being_checked():
    """A guard against this whole file silently passing on an empty set."""
    assert _admins_for_model_with_computed_fields(), (
        "No registered admin exposes a computed verification field — either the "
        "admin is unregistered or this test is no longer looking in the right place."
    )


def test_no_computed_field_is_editable_on_any_registered_admin(rf, superadmin):
    request = rf.get("/")
    request.user = superadmin

    for model, model_admin in _admins_for_model_with_computed_fields():
        readonly = set(model_admin.get_readonly_fields(request))
        model_fields = {f.name for f in model._meta.get_fields() if hasattr(f, "name")}

        for field in COMPUTED_VERIFICATION_FIELDS:
            if field in model_fields:
                assert field in readonly, (
                    f"{model_admin.__class__.__name__}.{field} is editable. It is computed by "
                    "directory.services.verification.recompute() and must be read-only."
                )


def test_no_computed_field_is_in_list_editable(rf, superadmin):
    """list_editable writes straight from the changelist, bypassing the form."""
    for model_admin in (a for _model, a in _admins_for_model_with_computed_fields()):
        editable = set(getattr(model_admin, "list_editable", ()) or ())
        leaked = editable & set(COMPUTED_VERIFICATION_FIELDS)

        assert not leaked, (
            f"{model_admin.__class__.__name__}.list_editable exposes {sorted(leaked)}. "
            "A changelist toggle is still a toggle."
        )


def test_no_computed_field_appears_in_a_generated_modelform(rf, superadmin):
    """The real check: what the generated form will accept from a POST.

    readonly_fields can look right while a field is re-enabled through
    `fields` or `fieldsets`. The form is the thing that decides.
    """
    request = rf.get("/")
    request.user = superadmin

    for _model, model_admin in _admins_for_model_with_computed_fields():
        form_class = model_admin.get_form(request, obj=None, change=False)
        leaked = set(form_class.base_fields) & set(COMPUTED_VERIFICATION_FIELDS)

        assert not leaked, (
            f"{model_admin.__class__.__name__}'s ModelForm accepts {sorted(leaked)}. "
            "That is a writable verification field."
        )


def test_no_computed_field_is_editable_on_any_inline(rf, superadmin):
    request = rf.get("/")
    request.user = superadmin

    for model_admin in admin.site._registry.values():
        for inline_class in getattr(model_admin, "inlines", ()):
            inline = inline_class(model_admin.model, admin.site)
            inline_fields = {f.name for f in inline.model._meta.get_fields() if hasattr(f, "name")}
            relevant = inline_fields & set(COMPUTED_VERIFICATION_FIELDS)
            if not relevant:
                continue

            readonly = set(inline.get_readonly_fields(request))
            for field in relevant:
                assert field in readonly, f"{inline_class.__name__}.{field} is editable through an inline."


def test_a_post_to_the_admin_cannot_set_the_badge(client, superadmin, settings):
    """End to end, through the actual HTTP surface.

    Everything above is introspection; this drives the real change view with a
    real POST, which is what an attacker or a well-meaning admin would do.
    """
    from django_otp.plugins.otp_totp.models import TOTPDevice

    practitioner = PractitionerFactory()
    assert practitioner.is_verified is False

    # Staff need a verified TOTP session to reach the admin at all (Phase 0).
    device = TOTPDevice.objects.create(user=superadmin, confirmed=True, name="default")
    client.force_login(superadmin)
    session = client.session
    session["otp_device_id"] = device.persistent_id
    session.save()

    url = f"/{settings.ADMIN_URL_PATH}/directory/practitioner/{practitioner.pk}/change/"
    response = client.post(
        url,
        {
            "slug": practitioner.slug,
            "full_name": practitioner.full_name,
            "status": practitioner.status,
            "is_verified": "on",
            "minor_work_status": "cleared",
        },
        follow=True,
    )

    assert response.status_code == 200
    practitioner.refresh_from_db()
    assert practitioner.is_verified is False, "A POST set the verified badge."
    assert practitioner.minor_work_status != "cleared", "A POST set minor_work_status."


def test_the_recompute_action_derives_rather_than_sets(rf, superadmin):
    """The one supported route changes state only by re-reading evidence."""
    from directory.admin import PractitionerAdmin

    practitioner = PractitionerFactory(verified=True)
    assert practitioner.is_verified is True

    # Remove the evidence, then recompute: the badge must go.
    practitioner.verifications.all().delete()

    model_admin = PractitionerAdmin(Practitioner, admin.site)
    request = rf.post("/")
    request.user = superadmin
    request._messages = _DummyMessages()

    model_admin.recompute_verification(request, Practitioner.objects.filter(pk=practitioner.pk))

    practitioner.refresh_from_db()
    assert practitioner.is_verified is False


class _DummyMessages:
    """django.contrib.messages needs a storage backend on the request."""

    def add(self, *args, **kwargs):
        return None


def test_anonymous_users_reach_no_admin_page(client, settings):
    response = client.get(f"/{settings.ADMIN_URL_PATH}/directory/practitioner/")
    assert response.status_code in (301, 302)
    assert AnonymousUser().is_anonymous
