---
description: Scaffold a new public page wired identically to the SEO base, JSON-LD and sitemap
---

Scaffold a new public page for the Kiam Clinic Directory. Ask for the URL path, page type and purpose if
not given as `$ARGUMENTS`.

Read `docs/seo.md` and `docs/content-compliance.md` first.

Produce, in this order:

1. **URL** in the owning app's `urls.py`, named, with a trailing slash.
2. **View** — thin. Any logic goes in `<app>/services/`.
3. **Template** extending the kiam-ui base (use the block names documented in
   `docs/design-system.md` — do not guess them).
4. **SEO block:** unique title, meta description, self-referencing canonical **within this
   subdomain**, OG/Twitter tags.
5. **JSON-LD** matching the type table in `docs/seo.md`. Never `AggregateRating`. Never
   `Physician`/`MedicalBusiness` for an independent practitioner.
6. **Sitemap:** add to the sitemap if indexable; add `noindex, follow` + canonical if it's a facet
   or filter permutation.
7. **Footer requirements:** crisis signposting. Plus, if the page shows practitioners, the
   independence notice verbatim.
8. **Test** covering: 200 response, canonical correctness, JSON-LD parses, and — where
   practitioners are rendered — that a `PROVISIONAL` practitioner's minor client groups are
   absent from the HTML.

Then run the `seo-reviewer` and `a11y-reviewer` subagents, and `compliance-reviewer` if the page
renders practitioner data. Report findings; do not auto-fix.
