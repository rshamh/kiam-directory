# CLAUDE.md — Kiam Clinic Directory

`directory.kiamclinic.com` · Django · own repo, own database, own deploy stack.

**Read before anything else:** `docs/multi-project-architecture.md` (what this project shares
with the main site and rooms, and what it must never share), then `docs/architecture.md`,
`docs/roadmap.md`, `docs/content-compliance.md`.

---

## What this project is

A public directory of **independent** mental-health practitioners. Kiam Clinic is the
**publisher and introducer only**. Clients contact and pay practitioners directly. Kiam does
not manage appointments, does not take payment, does not supervise care.

This is Model A (membership + relay, introducer-leaning), as signed off. Two changes would move
Kiam from introducer toward care provider or intermediary, and **neither may be built without
prior legal review**: taking payment, and triaging/matching clients to practitioners. If a task
seems to require either, stop and flag it.

---

## Golden rules

1. **SEO on every page.** Title, meta, canonical, OG, JSON-LD, heading order. Use `/new-page`.
   This subdomain owns its own `robots.txt`, `sitemap.xml`, `llms.txt` — never shared. Canonicals
   stay inside this subdomain.
2. **No prescription-only medication names in public copy.** Enforced by the taxonomy (which
   never contains one) and by the submission lint on free-text fields.
3. **Accessibility built in natively**, not bolted on. WCAG 2.2 AA. This audience has a high rate
   of neurodevelopmental and anxiety conditions: plain language, generous spacing, no
   time-limited interactions, no auto-carousels, `prefers-reduced-motion` respected.
4. **GDPR by design.** Minimal collection, explicit consent, spam protection, no trackers before
   consent.
5. **Independence framing is a hard requirement**, not a nicety. See below.
6. **Verification state is computed, never toggled.** See below.
7. Tests per app. Run `ruff check`, `ruff format --check` and `pytest` before every commit.
   Commit and push every change.

> **Not applicable to this project:** translation/i18n and RTL. English only, LTR only. Do not
> add `USE_I18N` machinery, `{% trans %}` tags, or logical-property gymnastics for RTL. (The main
> site's rules differ — do not copy them across.)

---

## Two rules specific to this project

### Independence framing
Every profile page and every search-results page carries this notice verbatim:

> Practitioners listed in the Kiam Clinic Directory are independent professionals. They are not
> employed by, or part of the clinical team at, Kiam Clinic. Clients arrange appointments and
> payment directly with the practitioner.

Contact reveal shows an interstitial confirming the same. Branding stays clearly related to Kiam
but visibly distinct. Never imply listed practitioners are Kiam clinicians.

### Verification is computed
`Practitioner.is_verified`, `credentials_checked_at`, `verification_expires_at` and
`minor_work_status` are derived from dated `VerificationCheck` rows by
`directory.services.verification.recompute()`. There is **no admin toggle and none may be
added** — the moment a human can set the badge, someone sets it as a favour and it stops meaning
anything. Do not expose these fields as editable in Django admin, forms, or the API.

Under-18 work is gated separately. A practitioner with a pending DBS may go live for **adult work
only**: their minor client groups are hidden from the profile and excluded from search. The gate
is applied in **two** places — `Practitioner.can_show_minor_groups` (profile) and the search
queryset. Either alone leaks. Any change to one requires a matching change and test in the other.

---

## Stack

- Django (latest production-ready), PostgreSQL + **PostGIS** via GeoDjango, Redis cache.
- Tailwind + **HTMX** + Alpine.js.
- **`kiam-ui`** — the shared UI package, pinned:
  `kiam-ui @ git+ssh://git@github.com/rshamh/kiam-ui@v1.0.1`
- Auth: email magic link, no usernames, thin custom `User`. All role checks in
  `accounts/access.py` and nowhere else.
- Search: `django.contrib.postgres.search` + `pg_trgm`; geo via GeoDjango `PointField` + GiST.
  **Do not add Meilisearch/Typesense** until latency actually demands it.
- Geocoding: `postcodes.io` for postcode → point; OS Open Names for place autocomplete. No Google
  Maps billing.
- Analytics: cookieless, fires only after consent.

## kiam-ui

Pinned to `v1.0.1`. **Upgrade deliberately** — bump the pin, read the changelog, run the visual
check, commit as its own change. Never track a branch.

**Before using it, read the installed package** (`pip show -f kiam-ui`, then read its templates,
template tags and settings) and write what it actually exposes into `docs/design-system.md`:
base template name, block names, the settings dict keys, and the component partials available.
Do not guess at its API from this file, and do not re-specify colours, type or spacing anywhere
in this repo — `kiam-ui` is the source of truth for those.

**Do not fork or vendor its components.** If something needs changing, raise it as a `kiam-ui`
change and bump the pin. A local override is a last resort and must be commented with why.

### Directory-specific components (build here, not in kiam-ui)
`SearchBar`, `LocationInput` (postcode/place autocomplete), `RadiusSelect`, `FilterSidebar`,
`FilterGroup`, `PractitionerCard`, `VerifiedBadge`, `TagGroup`, `ContactRevealPanel`,
`CompletenessMeter`, `VerificationStatusPanel`.

Build these as plain Django `{% include %}` partials in `templates/components/`, composed from
`kiam-ui` primitives. Propose one upward to `kiam-ui` only if another project needs it.

## HTMX conventions

The filter sidebar uses `hx-get` on `/search/` with `hx-push-url="true"` and
`hx-target="#results"`. The view returns the full page on a normal GET and
`partials/_results.html` when `request.htmx` is true.

**Non-JS and crawler requests must get the full page.** The filter UI degrades to a plain form
with a visible submit button. This is not optional — it is how the search page stays crawlable
and keyboard-usable.

Facet URLs are `noindex, follow` with a canonical to the base search page. The indexable surface
is the curated landing pages, not the facet engine.

---

## How to work here

- **Work in phases.** Follow `docs/roadmap.md`. Do not jump ahead or chain phases.
- **Pause at each gate.** Print what changed and what needs human confirmation, then stop.
- **Keep `docs/architecture.md` current** after every phase — app map, models, URLs, roles.
- The intent docs (`multi-project-architecture`, `design-system`, `seo`, `content-compliance`,
  `verification-policy`, `roadmap`) are human-authored. **Propose changes, don't silently
  rewrite them.**
- Migrations: one logical change per migration where practical, and always reviewable.

## Review subagents (run before closing a phase)

- **seo-reviewer** — title, meta, canonical, OG, JSON-LD, heading order, internal links,
  AEO answer-readiness, facet noindex rules, sitemap inclusion.
- **a11y-reviewer** — WCAG 2.2 AA: contrast, focus states, keyboard nav (filter sidebar and
  location combobox especially), alt text, form labels, reduced motion.
- **compliance-reviewer** — prescription-only medication names in public copy; efficacy or
  outcome claims; restricted titles without a matching verified registration; independence
  notice present on every profile and results page; **any published minor client group where
  `minor_work_status != CLEARED`**.

## Sign-off gates

- **Dr. Abbass** — all clinical content: speciality and category descriptions, landing-page
  editorial intros, verification-policy public copy, crisis signposting wording.
- **Solicitor** — Listing Agreement, terms of use, indemnity, retention periods.
- **CQC compliance lead** — independence framing, DPIA, verification policy.

Nothing clinical or legal publishes without its gate. Flag it, leave a `TODO(sign-off)` marker,
and continue — do not block on it.
