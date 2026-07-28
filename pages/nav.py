"""Chrome configuration for kiam-ui.

``KIAM_UI["NAV_ITEMS"]`` and ``KIAM_UI["FOOTER"]`` are set to the dotted paths of
the two callables below rather than to literal lists. The package resolves them
per request (``kiam_ui.conf.RESOLVABLE``), which is what lets the footer's crisis
signposting and the cross-links stay in Python — near the compliance rules they
implement — instead of being frozen into a settings dict.

Two things here are compliance controls, not content:

* the **crisis signposting** line, which ``docs/content-compliance.md`` §7
  requires in the footer of every public page, verbatim;
* the **independence** framing, which must be obvious rather than buried
  (CLAUDE.md, and ``docs/multi-project-architecture.md`` §5).

Both carry a ``TODO(sign-off)``. Neither may be reworded without its gate.
"""

from __future__ import annotations

from django.conf import settings
from django.urls import reverse

# TODO(sign-off): Dr. Abbass — crisis signposting wording.
# Verbatim from docs/content-compliance.md §7. Do not paraphrase, abbreviate, or
# move it behind a disclosure. Directory practitioners are not a crisis service
# and the site has to say so.
CRISIS_SIGNPOSTING = (
    "If you need urgent help, contact your GP, call 111, or call 999 in an emergency. "
    "Samaritans: 116 123. Practitioners listed here are not a crisis service."
)

# TODO(sign-off): CQC compliance lead — independence framing.
# The footer tagline. The full verbatim notice from docs/content-compliance.md §5
# belongs on profile and results pages (Phases 3 and 4); this is the always-on
# summary that sets the relationship on every page including this one.
INDEPENDENCE_TAGLINE = "A directory of independent mental-health practitioners, published by Kiam Clinic."


def _urls() -> dict[str, str]:
    return settings.KIAM_UI.get("URLS", {})


def nav_items(request):
    """The primary navigation.

    Thin at Phase 0 — Search, Browse and the static pages arrive with the phases
    that build them. The cross-link back to the main site is here from the start
    because it is how authority flows between the subdomains (docs/seo.md), and
    because a visitor needs an obvious route to the clinic that is *not* implied
    to be the same thing as the directory.
    """
    return [
        {"label": "Home", "url": reverse("pages:home")},
        {
            "label": "Kiam Clinic",
            "url": _urls().get("main", "https://kiamclinic.com"),
            "external": True,
            "aria_label": "Kiam Clinic main site (opens in the same tab)",
        },
    ]


def footer(request):
    """The footer, including the mandatory crisis signposting."""
    return {
        "TAGLINE": INDEPENDENCE_TAGLINE,
        # kiam-ui renders NOTE as the second, smaller line under the tagline —
        # the slot the main site uses for its CQC statement. Persistent and
        # non-alarming, as §7 requires.
        "NOTE": CRISIS_SIGNPOSTING,
        "COLUMNS": [
            {
                "heading": "Kiam",
                "links": [
                    {"label": "Kiam Clinic", "url": _urls().get("main", "https://kiamclinic.com")},
                    {"label": "Room rental", "url": _urls().get("rooms", "https://rooms.kiamclinic.com")},
                ],
            }
        ],
        "LEGAL_NAME": "KAZYS Ltd",
        # TODO(sign-off): solicitor — confirm the company number and the legal
        # entity named on this subdomain before launch.
        "COMPANY_NUMBER": "",
        # Terms, privacy, cookies and accessibility are Phase 3. Listing them
        # here before they exist would put 404s in the footer of every page.
        "LEGAL_LINKS": [],
    }
