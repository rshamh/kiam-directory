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

    The ``SearchAction`` arrived in Phase 4, with the view it points at. It
    advertises the ``q`` parameter only — the entry point, not the facet engine,
    which is ``noindex`` and is not a surface to invite a crawler into.
    """
    return {
        "@type": "WebSite",
        "@id": absolute("/#website"),
        "url": absolute("/"),
        "name": DIRECTORY_NAME,
        "publisher": {"@id": absolute("/#organization")},
        "inLanguage": "en-GB",
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": absolute("/search/") + "?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
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


# ===========================================================================
# Phase 3 — the practitioner profile
# ===========================================================================


def breadcrumb_items(*trail: tuple[str, str]) -> list[dict]:
    """The trail, as the kiam-ui breadcrumb partial wants it.

    ``BreadcrumbList`` is not built here, and that is deliberate. kiam-ui's
    ``components/navigation/breadcrumb.html`` emits the JSON-LD from the *same*
    list it renders the visible trail from, so the markup and the crumbs a
    visitor can see cannot drift apart. Building a second copy in this module
    would put two ``BreadcrumbList`` nodes on the page that agree only as long
    as somebody keeps them agreeing.

    What this function owns is the one thing the partial cannot: the URLs are
    absolute and on this subdomain, which is what the partial's own docstring
    asks for and what keeps the graph valid.
    """
    return [{"label": label, "url": absolute(path)} for label, path in trail]


def person(
    *,
    path: str,
    name: str,
    job_title: str = "",
    description: str = "",
    image_url: str = "",
    languages: list[str] | None = None,
    knows_about: list[str] | None = None,
    work_locations: list[dict] | None = None,
    honorific_prefix: str = "",
    honorific_suffix: str = "",
) -> dict:
    """A listed practitioner.

    ``Person`` is the type, and the only type. ``docs/seo.md`` rules out
    ``Physician`` and ``MedicalBusiness`` because these practitioners are
    independent professionals, not a Kiam clinic location — marking them up as
    one asserts in machine-readable form the exact relationship the project
    exists to deny. ``assert_no_ratings()`` enforces it rather than leaving it
    to whoever edits this next.

    Three things are deliberately absent:

    * **No ``worksFor`` / ``affiliation`` / ``memberOf`` pointing at the Kiam
      organisation node.** Same reason. Kiam publishes the listing; it does not
      employ the person in it.
    * **No ``email``, ``telephone`` or ``sameAs``.** Contact details are behind
      the reveal, and JSON-LD is HTML — putting them here would hand a scraper
      the address the reveal exists to protect, in a machine-readable envelope,
      before anyone clicked anything.
    * **No ``aggregateRating``.** There are no reviews and there never will be.
    """
    node = {
        "@type": "Person",
        "@id": absolute(path) + "#person",
        "name": name,
        "url": absolute(path),
    }
    if honorific_prefix:
        node["honorificPrefix"] = honorific_prefix
    if honorific_suffix:
        node["honorificSuffix"] = honorific_suffix
    if job_title:
        node["jobTitle"] = job_title
    if description:
        node["description"] = description
    if image_url:
        node["image"] = image_url
    if languages:
        node["knowsLanguage"] = languages
    if knows_about:
        node["knowsAbout"] = knows_about
    if work_locations:
        node["workLocation"] = work_locations
    return node


def place(
    *, name: str = "", street: str = "", locality: str = "", region: str = "", postcode: str = ""
) -> dict:
    """A practice address, for ``Person.workLocation``."""
    address = {"@type": "PostalAddress", "addressCountry": "GB"}
    if street:
        address["streetAddress"] = street
    if locality:
        address["addressLocality"] = locality
    if region:
        address["addressRegion"] = region
    if postcode:
        address["postalCode"] = postcode

    node = {"@type": "Place", "address": address}
    if name:
        node["name"] = name
    return node


def profile_page(*, path: str, name: str, description: str = "", modified=None) -> dict:
    """The page *about* the person, distinct from the person.

    ``ProfilePage`` + ``mainEntity`` is what tells a crawler this URL is one
    person's page rather than an article that mentions them, which is what gets
    the ``Person`` node attached to the right thing.
    """
    node = {
        "@type": "ProfilePage",
        "@id": absolute(path) + "#webpage",
        "url": absolute(path),
        "name": name,
        "isPartOf": {"@id": absolute("/#website")},
        "inLanguage": "en-GB",
        "mainEntity": {"@id": absolute(path) + "#person"},
    }
    if description:
        node["description"] = description
    if modified is not None:
        node["dateModified"] = modified.isoformat()
    return node


def search_results(*, path: str) -> SafeString:
    """The search page's own graph.

    A ``SearchResultsPage`` and nothing else. Emphatically **no ``ItemList``** of
    the practitioners on it: the page is ``noindex`` on every faceted URL, so marking
    up its contents would be describing listings on a page we have asked not to be
    indexed, and each of those people already has a ``Person`` node on their own
    profile. One canonical description per practitioner, on their own page.
    """
    return render(
        graph(
            {
                "@type": "SearchResultsPage",
                "@id": absolute(path) + "#webpage",
                "url": absolute(path),
                "name": "Search the Kiam Clinic Directory",
                "isPartOf": {"@id": absolute("/#website")},
                "inLanguage": "en-GB",
            }
        )
    )


def practitioner_profile(*, person_node: dict, page_node: dict) -> SafeString:
    """The profile's graph: ``Person`` + ``ProfilePage``.

    ``BreadcrumbList`` is the third type ``docs/seo.md`` asks for on this page
    and it arrives from the breadcrumb component — see ``breadcrumb_items``.
    """
    return render(graph(person_node, page_node))
