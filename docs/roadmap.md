# Roadmap

Eight phases, each with a gate. **Do not chain phases.** At a gate: print what changed, what
needs human confirmation, and stop.

Notion: mark the phase task *In Progress* when you start, *Done* with a one-line note when the
gate is signed off.

| Phase | Name | Gate reviews |
|---|---|---|
| 0 | Foundation | Project boots, kiam-ui renders, auth works |
| 1 | Data model & taxonomy | Migrations, seeded vocabulary, verification tests pass |
| 2 | Admin back office | A practitioner can be taken from invite to published |
| 3 | Public profile | A profile renders, is crawlable, gates minor groups correctly |
| 4 | Search | Geo + facets + ranking, degrades without JS |
| 5 | Home page | Search box, rotating grid |
| 6 | Practitioner dashboard | Self-service edit, evidence upload, one-click unpublish |
| 7 | Insights, landing pages, launch prep | a11y audit, DPIA, legal pack, pen test |

---

## Phase 0 — Foundation
Django project skeleton, settings split, PostGIS, Redis, Docker Compose, CI, `kiam-ui` wired and
documented, base template, thin `User` + magic-link auth, `apps/accounts/access.py`, SEO base
(`robots.txt`, `sitemap.xml`, `llms.txt`, JSON-LD helper), health check, error pages.

**Gate:** site boots on `localhost`, a magic link logs a user in, a kiam-ui-styled page renders,
`docs/design-system.md` documents kiam-ui's real API, CI green.

## Phase 1 — Data model & taxonomy
The models in `apps/directory/models.py` and `apps/accounts/models.py` (already written — review, don't
rewrite), migrations, `seed_taxonomy` management command, `apps/directory/services/verification.py`,
Django admin for staff, `nightly_sweep` management command + scheduler.

**Gate:** `seed_taxonomy` is idempotent; verification tests cover badge lapse and all four
`minor_work_status` transitions; no verification field is editable in admin.

## Phase 2 — Admin back office
Invite flow, submission review queue with lint flags, verification workbench (private evidence
with signed URLs + access logging), publish / request-changes / suspend, audit log viewer,
provisional-DBS grant with audit entry, concern-report queue.

**Gate:** a practitioner goes invite → draft → submitted → verified → published end to end;
evidence access is logged; suspend pulls a profile within seconds.

## Phase 3 — Public profile
`/p/<slug>/`, contact reveal with interstitial and metric increment, independence notice,
slug redirects, JSON-LD, sitemap entry, `/report-a-concern/`, static pages (about, terms,
privacy, cookies, accessibility, how-verification-works), crisis signposting in the footer.

**Gate:** compliance-reviewer passes; a `PROVISIONAL` practitioner's minor client groups are
absent from the rendered HTML.

## Phase 4 — Search
`/search/` with geo radius, all facets, ranking with banded shuffle, HTMX sidebar with full-page
fallback, location autocomplete via postcodes.io, empty and error states, facet noindex.

**Gate:** search works with JavaScript disabled; a minors filter returns only `CLEARED`
practitioners; ranking has tests.

## Phase 5 — Home page
Hero search (query + location side by side), radius control, daily-rotating grid of 12,
trust strip, independence notice, cross-links to the main site.

**Gate:** Core Web Vitals pass on a throttled connection; grid rotates daily and caches.

## Phase 6 — Practitioner dashboard
Overview with completeness meter and verification warnings, profile / locations / specialities /
credentials / availability / fees editors, evidence upload, insights, account & security,
**one-click unpublish**, re-review triggers on controlled fields.

**Gate:** unpublish takes effect immediately and writes a `ConsentRecord.withdrawn_at`; editing
a controlled field re-enters review.

## Phase 7 — Insights, landing pages, launch prep
Daily metric rollups, `/[speciality]/[town]` landing pages (only where ≥3 published practitioners
match **and** a signed-off editorial intro exists), full WCAG 2.2 AA audit, DPIA, legal pack,
quarterly register re-check job, pen test, backups, runbook.

**Gate:** solicitor and CQC compliance lead sign-off recorded; DPIA complete; no landing page
exists without an intro.
