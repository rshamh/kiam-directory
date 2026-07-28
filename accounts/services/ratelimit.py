"""A small fixed-window rate limiter on the cache.

Deliberately not a dependency. The only thing this project needs to limit is
magic-link requests, the rule is "N per key per window", and a fifteen-line
implementation on ``django.core.cache`` is easier to read and to test than a
configurable package. If a second, more complicated limit ever appears, revisit.

Fixed window rather than sliding: it is one round trip, it cannot be starved by a
burst at a boundary in any way that matters at these limits, and — the reason
that decides it — the failure mode is that an attacker gets at most 2N attempts
across a boundary instead of N. At N=5 magic links per hour that is not a
meaningful difference; the operational simplicity is.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from django.core.cache import cache

#: Namespace, so a limiter key can never collide with a page-cache key.
PREFIX = "ratelimit"


@dataclass(frozen=True)
class Result:
    """The outcome of one ``hit``."""

    allowed: bool
    count: int
    limit: int

    @property
    def exceeded(self) -> bool:
        return not self.allowed


def _key(scope: str, identifier: str) -> str:
    # The identifier is hashed rather than stored: an email address in a cache
    # key is personal data sitting in a store nobody thinks of as a database
    # (GDPR, golden rule #4), and it also keeps keys a fixed, memcached-safe size.
    digest = hashlib.sha256(identifier.strip().lower().encode("utf-8")).hexdigest()
    return f"{PREFIX}:{scope}:{digest}"


def hit(scope: str, identifier: str, *, limit: int, window_seconds: int) -> Result:
    """Count one attempt against ``identifier`` and say whether it is allowed.

    ``cache.add`` seeds the window with its TTL only when the key is absent, so
    the window starts at the first attempt and is never extended by later ones —
    an incr-then-expire ordering would let a steady stream of requests push the
    expiry out forever and the limit would never reset.
    """
    key = _key(scope, identifier)

    if cache.add(key, 1, timeout=window_seconds):
        return Result(allowed=limit >= 1, count=1, limit=limit)

    try:
        count = cache.incr(key)
    except ValueError:
        # The key expired between the `add` and the `incr`. That is a new window.
        cache.set(key, 1, timeout=window_seconds)
        count = 1

    return Result(allowed=count <= limit, count=count, limit=limit)


def peek(scope: str, identifier: str) -> int:
    """Attempts recorded so far, without recording one."""
    return cache.get(_key(scope, identifier), 0)


def reset(scope: str, identifier: str) -> None:
    """Clear a window. For tests and for an admin unblocking someone."""
    cache.delete(_key(scope, identifier))


def client_ip(request) -> str | None:
    """The client address, trusting ``X-Forwarded-For`` only behind our proxy.

    The left-most entry is the client as the *first* proxy saw it; entries can be
    forged by the client, so this is fit for rate limiting and abuse triage and is
    not fit for anything security-critical on its own.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR") or None
