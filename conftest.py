"""Shared pytest fixtures.

The cache fixture is autouse because the rate limiter is cache-backed: without
clearing it, the sixth magic-link test in a module would fail on a limit set by
the first five, and the failure would look like a bug in the code under test.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from accounts.models import User


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user_factory(db):
    def make(email="practitioner@example.com", role=User.Role.PRACTITIONER, **extra):
        return User.objects.create_user(email=email, role=role, **extra)

    return make


@pytest.fixture
def practitioner(user_factory):
    return user_factory("practitioner@example.com", User.Role.PRACTITIONER)


@pytest.fixture
def admin_user(user_factory):
    return user_factory("admin@example.com", User.Role.ADMIN)


@pytest.fixture
def verifier(user_factory):
    return user_factory("verifier@example.com", User.Role.VERIFIER)


@pytest.fixture
def superadmin(user_factory):
    return user_factory("super@example.com", User.Role.SUPERADMIN, is_staff=True, is_superuser=True)


@pytest.fixture
def verified_2fa():
    """Mark a user's session as having passed the TOTP challenge.

    ``django_otp``'s middleware sets ``user.otp_device``; the helpers in
    ``accounts.access`` read exactly that, so simulating it here keeps the tests
    honest about what they are asserting.
    """

    def apply(user):
        user.otp_device = object()
        return user

    return apply
