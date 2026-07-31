"""Signed-in devices: listing them, and ending one.

**Why there is a model at all.** Django's session table has no user column, so
"which sessions belong to me" can only be answered by decoding every row in the
table and reading `_auth_user_id` out of it — a full scan on every page load of
the security page, and one that reads other people's session payloads to do it.
`UserSession` mirrors the key and carries the three facts that make a row
recognisable to its owner: when it started, when it was last used, and from where.

**Recognisable is the whole requirement.** A list of four rows all saying "expires
in 12 days" cannot do the job a session list exists for, which is noticing the one
you do not recognise. So the user agent is rendered as "Firefox on macOS", and the
current session is marked as such — an unlabelled list where you cannot tell which
row is the browser you are reading it in invites people to revoke themselves.

**Revoking is deleting the session, not flagging it.** The Django session row goes,
so the next request from that device is anonymous. There is no "revoked" state to
get out of step with whether the session actually still works.

**No history.** The row is deleted when the session ends. A permanent record of
every device somebody has ever signed in from is a surveillance log, not a security
feature, and it would be the most sensitive table in this project.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.contrib.sessions.models import Session
from django.utils import timezone

from ..models import UserSession

logger = logging.getLogger("accounts.sessions")

#: Substrings → a name a person recognises. Order matters: Chrome's UA contains
#: "Safari", Edge's contains "Chrome", and every one of them contains "Mozilla".
_BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("Firefox/", "Firefox"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)

_PLATFORMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Macintosh", "macOS"),
    ("Mac OS X", "macOS"),
    ("Windows", "Windows"),
    ("Linux", "Linux"),
)


def describe(user_agent: str) -> str:
    """ "Firefox on macOS", or "Unknown device".

    Deliberately crude. A real UA parser is a dependency with a data file that
    goes stale, and the requirement here is only that somebody can tell their
    laptop from their phone.
    """
    agent = user_agent or ""
    browser = next((name for token, name in _BROWSERS if token in agent), "")
    platform = next((name for token, name in _PLATFORMS if token in agent), "")

    if browser and platform:
        return f"{browser} on {platform}"
    return browser or platform or "Unknown device"


@dataclass(frozen=True)
class Entry:
    session_key: str
    device: str
    ip: str
    created_at: object
    last_seen_at: object
    is_current: bool


def record(request) -> None:
    """Note the session this request arrived on. Called from the middleware.

    Cheap and idempotent: one `update_or_create` on an indexed unique column. It
    does not touch `django_session` at all — that is Django's, and duplicating its
    lifecycle would be two sources of truth about whether somebody is signed in.
    """
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return

    key = request.session.session_key
    if not key:
        return

    from .ratelimit import client_ip

    UserSession.objects.update_or_create(
        session_key=key,
        defaults={
            "user": user,
            "ip": client_ip(request) or None,
            # Truncated to the column width rather than left to raise on a
            # pathological header.
            "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:400],
        },
    )


def for_user(user, *, current_key: str = "") -> list[Entry]:
    """This user's live sessions, most recently used first.

    Rows whose Django session has already gone — expired, or flushed by a logout
    elsewhere — are cleaned up here rather than left to accumulate. A security page
    listing sessions that do not exist is worse than one that lists none.
    """
    rows = list(UserSession.objects.filter(user=user))
    if not rows:
        return []

    live_keys = set(
        Session.objects.filter(
            session_key__in=[r.session_key for r in rows],
            expire_date__gt=timezone.now(),
        ).values_list("session_key", flat=True)
    )

    stale = [r.pk for r in rows if r.session_key not in live_keys]
    if stale:
        UserSession.objects.filter(pk__in=stale).delete()

    return [
        Entry(
            session_key=row.session_key,
            device=describe(row.user_agent),
            ip=row.ip or "",
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            is_current=row.session_key == current_key,
        )
        for row in rows
        if row.session_key in live_keys
    ]


def revoke(user, session_key: str) -> bool:
    """End one session. Returns whether anything was ended.

    Scoped to `user` in the query itself, not checked afterwards: a session key is
    a bearer credential, and a function that could be made to delete somebody
    else's session by passing their key is a logout oracle at best.
    """
    owned = UserSession.objects.filter(user=user, session_key=session_key)
    if not owned.exists():
        return False

    Session.objects.filter(session_key=session_key).delete()
    owned.delete()

    logger.info("sessions.revoked", extra={"user_id": user.pk})
    return True


def revoke_all_except(user, session_key: str) -> int:
    """End every other session — the "sign out everywhere else" button.

    The one action worth having when somebody thinks their account is compromised,
    and it deliberately keeps the current session so they are not logged out of the
    page they are trying to use to secure the account.
    """
    others = UserSession.objects.filter(user=user).exclude(session_key=session_key)
    keys = list(others.values_list("session_key", flat=True))
    if not keys:
        return 0

    Session.objects.filter(session_key__in=keys).delete()
    others.delete()

    logger.warning("sessions.revoked_all", extra={"user_id": user.pk, "count": len(keys)})
    return len(keys)
