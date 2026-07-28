"""The thin ``User``, and the magic-link token it authenticates with.

Deliberately thin: email as the identifier, a role, and nothing else. No business
data hangs off the user — a practitioner's profile is a ``directory.Practitioner``
with a FK, not fields here. That is what keeps the later OIDC migration to a
single ``access.py`` change (docs/multi-project-architecture.md §3, Option B) and
what stops the user table becoming the place people bolt things onto.

There are no passwords. Authentication is a magic link; staff additionally carry a
TOTP device. ``set_unusable_password()`` is applied on creation so a password can
never be set through a route that forgets to.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    """Who someone is, in one place.

    Deliberately no client/patient role: clients never log in — the directory is
    a public read surface (docs/architecture.md, "Roles").

    ``VERIFIER`` is not "admin plus a flag". Approving profile copy and opening
    somebody's passport scan are different jobs, and the split is the control that
    keeps evidence access narrow.
    """

    PRACTITIONER = "practitioner", "Practitioner"
    ADMIN = "admin", "Admin"
    VERIFIER = "verifier", "Verifier"
    SUPERADMIN = "superadmin", "Superadmin"


#: Roles that must carry a confirmed TOTP device. Read by accounts.access and by
#: the 2FA enforcement middleware; do not test role strings anywhere else.
TWO_FACTOR_REQUIRED_ROLES = frozenset({Role.ADMIN.value, Role.VERIFIER.value, Role.SUPERADMIN.value})


class UserManager(BaseUserManager):
    """Creation goes through here so no route can invent a password."""

    use_in_migrations = True

    def _create(self, email: str, role: str, **extra):
        if not email:
            raise ValueError("A user must have an email address.")
        user = self.model(email=self.normalize_email(email).lower(), role=role, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email: str, role: str = Role.PRACTITIONER, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, role, **extra)

    def create_superuser(self, email: str, **extra):
        """Only ``createsuperuser`` reaches this. There is no signup route."""
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        if extra.get("is_staff") is not True or extra.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_staff=True and is_superuser=True.")
        return self._create(email, Role.SUPERADMIN, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """A person who can log in. Clients never appear here."""

    email = models.EmailField(
        "email address",
        unique=True,
        help_text="The identifier. There are no usernames anywhere in this project.",
    )
    full_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PRACTITIONER)

    is_active = models.BooleanField(
        default=True,
        help_text="Unticking this stops login immediately, magic link or not.",
    )
    is_staff = models.BooleanField(
        default=False,
        help_text="Django-admin access only. Application permissions come from `role`.",
    )

    date_joined = models.DateTimeField(default=timezone.now)
    last_login_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ("email",)
        indexes = [models.Index(fields=["role"])]

    def __str__(self) -> str:
        return self.email

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email

    def save(self, *args, **kwargs):
        # One canonical spelling of an address, so a magic link requested for
        # "Nadia@Example.com" cannot create a second account.
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)


class MagicLinkToken(models.Model):
    """A single-use, short-lived login token.

    Only the SHA-256 hash is stored. A database dump, a log line or a backup
    therefore contains nothing that can be replayed — the raw token exists once,
    in the email, and nowhere else.
    """

    #: Bytes of entropy in the raw token. 32 bytes -> a 43-character URL-safe
    #: string, well beyond guessing at any plausible request rate.
    TOKEN_BYTES = 32

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="magic_links")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    # Kept for abuse investigation only, and pruned with the row. Not analytics.
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    requested_user_agent = models.CharField(max_length=300, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"]),
            models.Index(fields=["user", "consumed_at"]),
        ]
        ordering = ("-created_at",)

    def __str__(self) -> str:
        # Never the token, and never the hash — this string reaches admin pages
        # and log lines.
        return f"magic link for user {self.user_id} ({self.created_at:%Y-%m-%d %H:%M})"

    @classmethod
    def new_raw_token(cls) -> str:
        return secrets.token_urlsafe(cls.TOKEN_BYTES)

    @classmethod
    def default_expiry(cls):
        return timezone.now() + timedelta(minutes=settings.MAGIC_LINK_TTL_MINUTES)

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_usable(self) -> bool:
        return not self.is_consumed and not self.is_expired
