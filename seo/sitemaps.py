"""This subdomain's sitemap.

Never shared with kiamclinic.com or rooms.kiamclinic.com — search engines treat
subdomains as separate sites, and each registers as its own Search Console
property (``docs/seo.md``).

What is in it is as much a decision as what is:

* **In** — the home page now; profiles (Phase 3), curated ``/[speciality]/[town]``
  landing pages and browse indexes (Phase 7), and the static pages (Phase 3).
* **Out** — every faceted search URL. Those are ``noindex, follow`` with a
  canonical to ``/search/``. Listing them would generate millions of thin
  permutations and get the subdomain read as a doorway farm.

A sitemap entry is a claim that a URL is worth indexing. Adding a class here
without checking it against ``docs/seo.md`` "Indexable vs not" is how that claim
stops being true.
"""

from __future__ import annotations

from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    """Pages that always exist and are always indexable."""

    changefreq = "weekly"
    protocol = "https"

    def items(self) -> list[str]:
        # Phase 3 adds about / terms / privacy / cookies / accessibility /
        # how-verification-works here.
        return ["pages:home"]

    def location(self, item: str) -> str:
        return reverse(item)

    def priority(self, item: str) -> float:
        return 1.0 if item == "pages:home" else 0.5


#: Registered with the sitemap views in config/urls.py.
SITEMAPS = {"static": StaticViewSitemap}
