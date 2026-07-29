"""The static page registry.

One row per page: the URL, the URL name, the title, the meta description and the
template. Eight near-identical views would have been eight places to forget a
meta description — golden rule #1 is easier to keep when the SEO metadata for
every static page is in one list somebody can read top to bottom.

The registry is also what ``seo.sitemaps`` iterates, so a page cannot be added to
the site and forgotten by the sitemap: adding it here does both. That is the
point. ``/report-a-concern/`` is the one exception — it takes a POST and has its
own view, so it is listed in ``FORM_PAGES`` and joins the sitemap from there.

Page *bodies* are in ``templates/pages/``. Anything clinical or legal in them
carries a ``TODO(sign-off)`` marker and names its gate.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StaticPage:
    name: str
    path: str
    title: str
    description: str
    template: str
    #: Sitemap priority. The pages a stranger actually needs rank above the ones
    #: they only reach from a footer.
    priority: float = 0.4


STATIC_PAGES: tuple[StaticPage, ...] = (
    StaticPage(
        name="about",
        path="about/",
        title="About the Kiam Clinic Directory",
        description=(
            "The Kiam Clinic Directory is a public listing of independent mental-health "
            "practitioners. Kiam Clinic publishes the listings and introduces nothing else — "
            "clients contact and pay practitioners directly."
        ),
        template="pages/about.html",
        priority=0.7,
    ),
    StaticPage(
        name="how_verification_works",
        path="how-verification-works/",
        title="How verification works",
        description=(
            "What “Credentials checked” means on a Kiam Clinic Directory listing, what it "
            "does not mean, which documents are checked, and how a badge lapses."
        ),
        template="pages/how_verification_works.html",
        priority=0.7,
    ),
    StaticPage(
        name="for_practitioners",
        path="for-practitioners/",
        title="For practitioners",
        description=(
            "How independent mental-health practitioners are listed in the Kiam Clinic "
            "Directory, what evidence is required, and what a listing does and does not do."
        ),
        template="pages/for_practitioners.html",
        priority=0.6,
    ),
    StaticPage(
        name="accessibility",
        path="accessibility/",
        title="Accessibility",
        description=(
            "How the Kiam Clinic Directory is built to be usable — display settings, keyboard "
            "operation, reduced motion — and how to tell us when something is not."
        ),
        template="pages/accessibility.html",
    ),
    # The three legal pages had descriptions of 41-52 characters. Unique, so not
    # a duplication problem, but short enough that Google synthesises its own
    # from the page — which on a placeholder page means a snippet built from the
    # "in preparation" notice.
    StaticPage(
        name="terms",
        path="terms/",
        title="Terms of use",
        description=(
            "The terms on which the Kiam Clinic Directory is published and used, what a "
            "listing is and is not, and the basis on which practitioners are listed."
        ),
        template="pages/terms.html",
    ),
    StaticPage(
        name="privacy",
        path="privacy/",
        title="Privacy notice",
        description=(
            "What personal data the Kiam Clinic Directory holds about listed practitioners "
            "and about visitors, why, and for how long. No account is needed to read it and "
            "there is no per-visitor tracking."
        ),
        template="pages/privacy.html",
    ),
    StaticPage(
        name="cookies",
        path="cookies/",
        title="Cookies",
        description=(
            "What the Kiam Clinic Directory stores in your browser and why — display "
            "preferences, sign-in and form security. No advertising cookies and no "
            "cross-site tracking."
        ),
        template="pages/cookies.html",
    ),
)

BY_NAME = {page.name: page for page in STATIC_PAGES}

#: Indexable pages that take a POST and therefore have their own view.
FORM_PAGES = ("report_concern",)

#: Sitemap priorities for the form pages, keyed by URL name.
FORM_PAGE_PRIORITY = {"report_concern": 0.4}
