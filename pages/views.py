"""Public content pages.

At Phase 0 there is exactly one: a placeholder home page that proves the kiam-ui
chrome renders and that the SEO base is wired. The real home page — hero search,
rotating grid, trust strip — is Phase 5 (docs/roadmap.md).
"""

from __future__ import annotations

from django.shortcuts import render
from django.views.decorators.http import require_GET

from seo import jsonld


@require_GET
def home(request):
    return render(
        request,
        "pages/home.html",
        {
            "meta_title": "Find an independent mental-health practitioner",
            "meta_description": (
                "A directory of independent mental-health practitioners, published by "
                "Kiam Clinic. Search by speciality and location, and contact "
                "practitioners directly."
            ),
            "jsonld": jsonld.home(),
        },
    )
