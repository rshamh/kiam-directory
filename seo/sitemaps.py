"""This subdomain's sitemap.

Never shared with kiamclinic.com or rooms.kiamclinic.com — search engines treat
subdomains as separate sites, and each registers as its own Search Console
property (``docs/seo.md``).

What is in it is as much a decision as what is:

* **In** — the home page, the static pages, ``/report-a-concern/``, the bare
  ``/search/``, and every PUBLISHED practitioner profile. Curated
  ``/[speciality]/[town]`` landing pages and browse indexes join them in Phase 7.
* **Out** — every faceted search URL. Those are ``noindex, follow`` with a
  canonical to ``/search/``. Listing them would generate millions of thin
  permutations and get the subdomain read as a doorway farm.

The bare ``/search/`` is in because ``docs/seo.md`` requires every facet
permutation to canonicalise **to** it, and a canonical pointing at a URL we have
told nobody about is not a signal — it is a dead end. It was left out at first,
which the Phase 4 SEO review caught: the page was declared indexable, absent from
here, and linked from nowhere on the site, so nothing could reach it.

> **Proposed amendment to `docs/seo.md`, for a human.** Its "Indexable vs not"
> list names home, profiles, curated landing pages, browse indexes and static
> pages — it does not mention ``/search/`` either way, while the facet rule
> implicitly makes it a canonical target. The coherent alternative is to make ALL
> of ``/search/`` ``noindex, follow`` and drop it from here, which is closer to
> Google's own guidance on internal search results. That is an intent decision,
> not an implementation one.
* **Out** — every listing that is not PUBLISHED. A sitemap entry for a suspended
  profile invites a crawler to fetch a 404 and, worse, tells anyone who reads the
  sitemap that a named person was listed and is not any more.

A sitemap entry is a claim that a URL is worth indexing. Adding a class here
without checking it against ``docs/seo.md`` "Indexable vs not" is how that claim
stops being true.
"""

from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from directory.models import Practitioner, PublicationStatus
from directory.services.profile import profile_path
from pages import content


class StaticViewSitemap(Sitemap):
    """Pages that always exist and are always indexable.

    Driven off ``pages.content.STATIC_PAGES`` rather than a hand-kept list, so a
    page cannot be added to the site and forgotten here.
    """

    changefreq = "weekly"
    protocol = "https"

    def items(self) -> list[str]:
        return [
            "pages:home",
            # The BARE search page only. Facet permutations are noindex and
            # canonicalise here; this is the URL they canonicalise to.
            "search:search",
            *[f"pages:{page.name}" for page in content.STATIC_PAGES],
            *[f"pages:{name}" for name in content.FORM_PAGES],
        ]

    def location(self, item: str) -> str:
        return reverse(item)

    def priority(self, item: str) -> float:
        if item == "pages:home":
            return 1.0
        if item == "search:search":
            # Below the home page, above the static content. It is the entry point
            # a stranger actually needs.
            return 0.9
        name = item.split(":", 1)[1]
        if name in content.BY_NAME:
            return content.BY_NAME[name].priority
        return content.FORM_PAGE_PRIORITY.get(name, 0.4)


class PractitionerSitemap(Sitemap):
    """Published profiles.

    ``limit`` is left at Django's default: the framework paginates a large
    sitemap into an index automatically, and the directory would have to grow by
    two orders of magnitude before that mattered.
    """

    changefreq = "weekly"
    priority = 0.8
    protocol = "https"

    def items(self):
        return (
            Practitioner.objects.filter(status=PublicationStatus.PUBLISHED)
            .only("slug", "updated_at")
            .order_by("slug")
        )

    def location(self, item: Practitioner) -> str:
        # Not `get_absolute_url()`: directory/models.py is adopted verbatim from
        # elsewhere and additions to it go in a marked block, never interleaved
        # into a class (CLAUDE.md). The path lives with the rest of the profile
        # logic instead.
        return profile_path(item)

    def lastmod(self, item: Practitioner):
        return item.updated_at


#: Registered with the sitemap views in config/urls.py.
SITEMAPS = {"static": StaticViewSitemap, "practitioners": PractitionerSitemap}
