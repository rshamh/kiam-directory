"""``robots.txt``, ``llms.txt`` and the health check.

Search engines treat ``directory.kiamclinic.com`` as a separate site, so this
subdomain owns all three of ``robots.txt``, ``sitemap.xml`` and ``llms.txt``.
None is shared with the main site or with rooms (``docs/seo.md``).
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def robots_txt(request):
    """Served from a template so the disallow list is reviewable as content."""
    return render(
        request,
        "seo/robots.txt",
        {"sitemap_url": _absolute(request, "/sitemap.xml")},
        content_type="text/plain; charset=utf-8",
    )


@require_GET
def llms_txt(request):
    """What this directory is, for an AI crawler.

    The independence relationship is stated here explicitly. An answer engine
    summarising the site without it would describe these practitioners as Kiam
    clinicians, which is the exact misrepresentation the project has to avoid
    (CLAUDE.md, docs/seo.md "AEO / AI search").
    """
    return render(
        request,
        "seo/llms.txt",
        {"base_url": settings.SITE_BASE_URL},
        content_type="text/plain; charset=utf-8",
    )


def _absolute(request, path: str) -> str:
    return f"{request.scheme}://{request.get_host()}{path}"


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


@never_cache
@require_GET
def healthz(request):
    """App, database and cache status.

    Returns 200 only when all three are up; 503 otherwise, so a load balancer
    takes a broken instance out rather than serving 500s from it. Deliberately
    terse — it is unauthenticated, so it must not describe the estate.
    """
    checks = {"app": "ok", "database": _check_database(), "cache": _check_cache()}
    healthy = all(value == "ok" for value in checks.values())

    if not healthy:
        logger.error("healthz.unhealthy", extra={"checks": checks})

    response = JsonResponse({"status": "ok" if healthy else "degraded", "checks": checks})
    response.status_code = 200 if healthy else 503
    return response


def _check_database() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        logger.exception("healthz.database_failed")
        return "error"
    return "ok"


def _check_cache() -> str:
    from django.core.cache import cache

    try:
        cache.set("healthz", "ok", timeout=10)
        if cache.get("healthz") != "ok":
            return "error"
    except Exception:
        logger.exception("healthz.cache_failed")
        return "error"
    return "ok"


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


def not_found(request, exception=None):  # noqa: ARG001
    return render(request, "404.html", status=404)


def server_error(request):
    """500 handler.

    Rendered with ``render_to_string`` and no context processors, because a 500
    can be *caused* by a context processor or a database that is down — and a
    template that needs either would then fail while rendering the error page,
    turning a handled 500 into a bare traceback. The page is deliberately
    self-contained; see the comment in templates/500.html.
    """
    from django.template.loader import render_to_string

    return HttpResponse(render_to_string("500.html"), status=500, content_type="text/html")
