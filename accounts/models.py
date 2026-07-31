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


# ===========================================================================
# Phase 6 additions — account security
# ===========================================================================
# Everything above is the authored file, byte-identical. These two models are
# appended rather than interleaved, per CLAUDE.md ("Additions go in a clearly
# marked block at the end of the file, not interleaved").
#
# Both exist for the dashboard's "Account & security" page, and both hold
# personal data, so both say what they hold and why.


class UserSession(models.Model):
    """One signed-in device, so a practitioner can see and end their own sessions.

    Django's `django.contrib.sessions` table is keyed on an opaque session key and
    carries no user column, no timestamps beyond expiry, and nothing about the
    device. A "session list" built from it alone reads as four identical rows
    saying "expires in 12 days", which is useless for the thing a session list is
    for: noticing one you do not recognise.

    So this mirrors the session key and adds the three facts that make a row
    identifiable — when it started, when it was last used, and from where.

    **On the personal data.** IP and user agent are personal data, held on the
    lawful basis of securing the account they belong to, visible only to that
    account's owner, and deleted with the session. That is a narrow and ordinary
    justification, but it is a new category of data for this project, so it is
    named here and belongs in the privacy notice.
    TODO(sign-off): solicitor — /privacy/ is still an outline; this needs a line.

    The row is deleted when the session is revoked or expires. There is no history
    of past sessions, deliberately: a permanent log of every device somebody has
    ever signed in from is a surveillance record, not a security feature.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")
    session_key = models.CharField(max_length=40, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "-last_seen_at"])]

    def __str__(self):
        return f"{self.user_id} @ {self.ip or 'unknown'}"


class EmailChangeRequest(models.Model):
    """A pending email change, confirmed at BOTH addresses.

    The email address is the identifier this project authenticates on — there are
    no usernames — so changing it is changing the credential. Confirming at only
    the new address lets anyone with a momentarily unattended session move the
    account somewhere the owner cannot follow; confirming at only the old one lets
    a typo lock the account out for good. Both, or nothing happens.

    Token discipline is `accounts.services.magic_link`'s, deliberately: two
    independent CSPRNG tokens, only the SHA-256 hashes stored, single use, and the
    whole request expires. A confirmation link is a credential.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_changes")
    new_email = models.EmailField()

    current_token_hash = models.CharField(max_length=128, unique=True)
    new_token_hash = models.CharField(max_length=128, unique=True)

    confirmed_current_at = models.DateTimeField(null=True, blank=True)
    confirmed_new_at = models.DateTimeField(null=True, blank=True)

    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    requested_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "-created_at"])]

    @property
    def is_open(self) -> bool:
        return self.completed_at is None and self.cancelled_at is None and self.expires_at > timezone.now()

    @property
    def is_fully_confirmed(self) -> bool:
        return self.confirmed_current_at is not None and self.confirmed_new_at is not None
