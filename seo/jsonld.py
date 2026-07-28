"""JSON-LD builders.

One place that knows the shapes, so a page template never hand-writes structured
data and cannot invent a type. ``docs/seo.md`` fixes which type belongs on which
page:

    home                     WebSite + Organization
    profile                  Person + ProfilePage + BreadcrumbList   (Phase 3)
    speciality/town landing  CollectionPage + BreadcrumbList         (Phase 7)
    static                   WebPage

Two prohibitions, both from ``docs/seo.md``, enforced here rather than left to
reviewer diligence:

* **No ``AggregateRating``.** There are no reviews and there never will be.
  ``assert_no_ratings()`` is called on every graph this module emits.
* **No ``Physician`` / ``MedicalBusiness`` on a practitioner.** They are
  independent professionals, not a Kiam clinic location; marking them up as one
  misrepresents the relationship the whole project exists to keep clear.
  ``Person`` is the honest type. ``PROHIBITED_TYPES`` is checked too.

Canonicals and ``@id`` values stay inside this subdomain — never across to
kiamclinic.com (``docs/multi-project-architecture.md`` §5).
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

#: The shared NAP, identical across the three subdomains (docs/seo.md).
ORGANISATION_NAME = "Kiam Clinic"
DIRECTORY_NAME = "Kiam Clinic Directory"

PROHIBITED_TYPES = frozenset({"AggregateRating", "Review", "Physician", "MedicalBusiness"})


class ProhibitedStructuredData(ValueError):
    """A graph contained a type docs/seo.md forbids on this site."""


def absolute(path: str = "/") -> str:
    """An absolute URL on this subdomain."""
    return urljoin(settings.SITE_BASE_URL.rstrip("/") + "/", path.lstrip("/"))


def _walk_types(node: Any):
    if isinstance(node, dict):
        value = node.get("@type")
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (v for v in value if isinstance(v, str))
        for child in node.values():
            yield from _walk_types(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk_types(child)


def assert_no_ratings(graph: Any) -> None:
    """Raise if a forbidden type appears anywhere in the graph."""
    found = set(_walk_types(graph)) & PROHIBITED_TYPES
    if found:
        raise ProhibitedStructuredData(
            f"docs/seo.md forbids {sorted(found)} on this site. "
            "There are no reviews, and listed practitioners are independent — "
            "Person is the honest type."
        )


def organisation() -> dict:
    """The publisher. Same NAP as the main site and rooms."""
    contact = settings.KIAM_UI.get("CONTACT", {})
    return {
        "@type": "Organization",
        "@id": absolute("/#organization"),
        "name": ORGANISATION_NAME,
        "url": "https://kiamclinic.com/",
        "telephone": contact.get("phone", ""),
        "email": contact.get("email", ""),
        "address": {
            "@type": "PostalAddress",
            "streetAddress": contact.get("street", ""),
            "addressLocality": contact.get("locality", ""),
            "addressRegion": contact.get("region", ""),
            "postalCode": contact.get("postcode", ""),
            "addressCountry": "GB",
        },
    }


def website() -> dict:
    """This subdomain as a site in its own right.

    No ``SearchAction``: pointing one at ``/search/`` before that view exists
    advertises a 404, and the facet engine is not the indexable surface anyway
    (docs/seo.md). Add it in Phase 4.
    """
    return {
        "@type": "WebSite",
        "@id": absolute("/#website"),
        "url": absolute("/"),
        "name": DIRECTORY_NAME,
        "publisher": {"@id": absolute("/#organization")},
        "inLanguage": "en-GB",
    }


def web_page(*, path: str, name: str, description: str = "") -> dict:
    return {
        "@type": "WebPage",
        "@id": absolute(path) + "#webpage",
        "url": absolute(path),
        "name": name,
        "description": description,
        "isPartOf": {"@id": absolute("/#website")},
        "inLanguage": "en-GB",
    }


def graph(*nodes: dict) -> dict:
    """Assemble a ``@graph`` document, checked against the prohibitions."""
    document = {"@context": "https://schema.org", "@graph": [n for n in nodes if n]}
    assert_no_ratings(document)
    return document


def render(document: dict) -> SafeString:
    """The document as a ready-to-insert ``<script>`` tag.

    ``ensure_ascii`` keeps non-ASCII escaped, and ``</`` is broken up so a name
    containing ``</script>`` cannot close the tag early — the one XSS route that
    JSON-LD actually has.
    """
    assert_no_ratings(document)
    payload = json.dumps(document, ensure_ascii=True).replace("</", "<\\/")
    return mark_safe(f'<script type="application/ld+json">{payload}</script>')  # noqa: S308


def home() -> SafeString:
    return render(graph(website(), organisation()))


def static_page(*, path: str, name: str, description: str = "") -> SafeString:
    return render(graph(web_page(path=path, name=name, description=description)))
