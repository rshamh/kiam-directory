"""
Thin user model.

Per docs/multi-project-architecture.md §3 (Option A — separate auth per project):
keep this model THIN. Email as identifier, no business data, no profile fields.
Everything about a practitioner lives on directory.Practitioner, linked by FK.

This is what makes the later OIDC migration (Option B, main site as identity provider)
a link-by-verified-email exercise rather than a rewrite. Never assume a user id here
matches a user id in the main site or rooms.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_unusable_password()  # magic link only — no passwords anywhere
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("role", User.Role.SUPERADMIN)
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        user = self.model(email=self.normalize_email(email), **extra)
        # Django admin needs a password; app login remains magic-link only.
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        PRACTITIONER = "practitioner", "Practitioner"
        ADMIN = "admin", "Admin"
        VERIFIER = "verifier", "Verifier"  # may view private evidence documents
        SUPERADMIN = "superadmin", "Superadmin"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PRACTITIONER)
    display_name = models.CharField(max_length=120, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    totp_enabled = models.BooleanField(default=False)  # mandatory for staff roles
    last_login_at = models.DateTimeField(null=True, blank=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        indexes = [models.Index(fields=["role"])]

    def __str__(self):
        return self.email


class LoginToken(models.Model):
    """Single-use magic link. Store the hash, never the token."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_tokens")
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()


class Invite(models.Model):
    """
    Admin-issued invitation. Phase 1 is invite-only: there is no public signup route
    and none should be added until the public-registration phase, which needs stricter
    identity checks than an email round-trip.
    """

    email = models.EmailField()
    token_hash = models.CharField(max_length=128, unique=True)
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="invites_sent")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["email"])]
