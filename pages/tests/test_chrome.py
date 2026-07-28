"""The kiam-ui chrome renders, and the compliance copy it carries is present.

These are the "chrome-wiring tests" kiam-ui's README asks a consumer to keep:
they are what catches a configuration key that changed shape at the next pin bump.
"""

from __future__ import annotations

import pytest
from django.urls import reverse

from pages import nav

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The package is actually wired
# ---------------------------------------------------------------------------


def test_the_home_page_renders_the_kiam_ui_chrome(client):
    body = client.get(reverse("pages:home")).content.decode()

    assert 'class="site-header site-header--directory"' in body
    assert '<footer class="site-footer">' in body
    assert 'href="/static/kiam_ui/css/kiam-ui.css"' in body


def test_the_site_stylesheet_loads_after_the_shared_layer(client):
    """Site CSS must win a genuine collision (docs/design-system.md §6)."""
    body = client.get(reverse("pages:home")).content.decode()

    assert body.index("kiam_ui/css/kiam-ui.css") < body.index("css/app.css")


def test_the_accessibility_panel_is_present(client):
    body = client.get(reverse("pages:home")).content.decode()

    assert 'id="a11y-panel"' in body
    assert 'aria-label="Display and accessibility settings"' in body


def test_the_skip_link_is_first_in_the_body(client):
    body = client.get(reverse("pages:home")).content.decode()
    assert 'class="skip-link" href="#main"' in body


def test_the_language_switcher_is_off(client):
    """English only, LTR only — and it would post to a URL we do not wire."""
    body = client.get(reverse("pages:home")).content.decode()

    assert "lang-switch" not in body
    assert "set_language" not in body


def test_alpine_and_htmx_are_self_hosted(client):
    """Nothing third-party may load before a consent decision (golden rule #4)."""
    body = client.get(reverse("pages:home")).content.decode()

    assert "/static/kiam_ui/js/alpine.min.js" in body
    assert "/static/kiam_ui/js/htmx.min.js" in body
    assert "cdn.jsdelivr.net" not in body
    assert "unpkg.com" not in body


# ---------------------------------------------------------------------------
# Compliance copy carried by the chrome
# ---------------------------------------------------------------------------


def test_the_crisis_signposting_is_in_the_footer(client):
    """docs/content-compliance.md §7 — persistent, on every public page."""
    body = client.get(reverse("pages:home")).content.decode()

    assert "call 111, or call 999 in an emergency" in body
    assert "Samaritans: 116 123" in body


def test_the_crisis_line_matches_content_compliance_verbatim():
    assert nav.CRISIS_SIGNPOSTING.startswith(
        "If you need urgent help, contact your GP, call 111, or call 999 in an emergency. "
        "Samaritans: 116 123."
    )


def test_the_independence_notice_is_verbatim(client):
    """docs/content-compliance.md §5. Not paraphrased, not abbreviated."""
    body = " ".join(client.get(reverse("pages:home")).content.decode().split())

    assert (
        "Practitioners listed in the Kiam Clinic Directory are independent professionals. "
        "They are not employed by, or part of the clinical team at, Kiam Clinic. Clients "
        "arrange appointments and payment directly with the practitioner." in body
    )


def test_the_disclaimer_bar_uses_our_copy_not_the_clinics(client):
    body = " ".join(client.get(reverse("pages:home")).content.decode().split())

    assert "Practitioners listed in this directory are independent and are not an emergency service" in body
    # The packaged clinic sentence must not appear.
    assert "is not an acute or emergency service" not in body


def test_the_chrome_never_calls_the_directory_a_clinic(client):
    body = client.get(reverse("pages:home")).content.decode()

    assert "Kiam Directory" in body
    # The brand wordmark is the directory's, not the clinic's.
    assert '<span class="brand-name">Kiam <span class="brand-name-accent">Directory</span>' in body


# ---------------------------------------------------------------------------
# Navigation and footer configuration
# ---------------------------------------------------------------------------


def test_nav_items_have_the_shape_kiam_ui_expects(rf):
    items = nav.nav_items(rf.get("/"))

    assert items
    for item in items:
        assert "label" in item
        assert "url" in item


def test_the_nav_cross_links_to_the_main_site(rf):
    """How authority flows between the subdomains (docs/seo.md)."""
    urls = [item["url"] for item in nav.nav_items(rf.get("/"))]
    assert "https://kiamclinic.com" in urls


def test_the_footer_has_no_links_to_pages_that_do_not_exist_yet(rf, client):
    footer = nav.footer(rf.get("/"))
    assert footer["LEGAL_LINKS"] == []

    for column in footer["COLUMNS"]:
        for link in column["links"]:
            if link["url"].startswith("/"):
                assert client.get(link["url"]).status_code == 200


def test_there_is_no_booking_cta(client, settings):
    """Kiam takes no payment and manages no appointments (CLAUDE.md)."""
    assert settings.KIAM_UI["PRIMARY_CTA"] is None

    body = client.get(reverse("pages:home")).content.decode().lower()
    assert "book an appointment" not in body


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


def test_healthz_reports_app_database_and_cache(client):
    response = client.get(reverse("seo:healthz"))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"] == {"app": "ok", "database": "ok", "cache": "ok"}


def test_healthz_is_never_cached(client):
    response = client.get(reverse("seo:healthz"))
    assert "no-cache" in response["Cache-Control"]


def test_healthz_returns_503_when_the_cache_is_down(client, monkeypatch):
    """A degraded instance must be pulled from the pool, not left serving 500s."""
    from seo import views

    monkeypatch.setattr(views, "_check_cache", lambda: "error")

    response = client.get(reverse("seo:healthz"))
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
