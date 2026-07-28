# Architecture

> **Status: intent seed.** Human-authored to describe the intended structure. Claude Code MUST
> keep it current — after each phase, update the app map, models, URLs and roles matrix to
> reflect what actually exists in the repo.

See `docs/multi-project-architecture.md` for how this sits alongside the main site and rooms.

## Apps

| App | Responsibility |
|---|---|
| `accounts` | Thin `User` (email identifier, no business data), magic-link auth, `Invite`, and **all** role predicates in `access.py` |
| `directory` | Practitioners, locations, taxonomy, credentials, verification, consent, review workflow, audit, metrics |
| `search` | Query parsing, geocoding, the search view and HTMX partials |
| `dashboard` | Practitioner self-service |
| `backoffice` | Admin queues: invites, review, verification workbench, suspensions, concern reports |
| `seo` | Sitemaps, robots, llms.txt, JSON-LD helpers, `/new-page` scaffold output |
| `pages` | Static content pages and the curated landing pages |

Business logic lives in `<app>/services/`, not in views or models. Views stay thin.

## Why the taxonomy is four lists, not one

| Axis | Answers | Example |
|---|---|---|
| `Profession` | What are they? | Consultant Psychiatrist |
| `SpecialityCategory` → `Speciality` | What do they treat? | Neurodevelopmental → Adult ADHD assessment |
| `Approach` | How do they work? | CBT, EMDR |
| `ClientGroup` | Who do they see? | Adults, Adolescents (12–17) |

Collapse these into one tag list and the filter sidebar becomes unusable at roughly a hundred
listings. Plus flat vocabularies: `Language`, `FundingOption`, `SessionFormat`.

## Three load-bearing model decisions

1. **`PractitionerLocation` is a FK collection, not fields on `Practitioner`.** A therapist may
   work Tuesdays in Epsom and Thursdays in Guildford, and must surface in both searches. This is
   the one modelling mistake that is genuinely expensive to retrofit.
2. **Online delivery is independent of location.** `offers_online` + `online_coverage` with no
   physical address is a valid, complete listing.
3. **Publication is a state machine**, not a boolean:
   `DRAFT → SUBMITTED → IN_REVIEW → (CHANGES_REQUESTED | APPROVED) → PUBLISHED →
   (SUSPENDED | UNPUBLISHED | REMOVED)`.
   Edits to a published profile: safe fields (bio, availability, photo) publish immediately;
   **controlled fields** (name, credentials, profession, registration numbers, client groups)
   re-enter review.

## Roles

> No client/patient role. Clients never log in — the directory is a public read surface.

| Role | Can |
|---|---|
| `practitioner` | Edit own profile, upload evidence, view own insights, unpublish own listing |
| `admin` | Invite, review submissions, publish, suspend, manage taxonomy |
| `verifier` | Everything admin does, **plus** open private evidence documents and grant provisional DBS |
| `superadmin` | Everything, plus Django admin |

Deliberately, `admin` cannot open someone's passport scan — approving profile copy and inspecting
identity documents are different jobs. Every evidence access is logged (`DocumentAccessLog`).

All predicates live in `accounts/access.py`. Views and templates never inspect `user.role`
directly. That is what makes the later OIDC migration a one-file change.

## Storage

Two separate backends, never merged:
- **Public** — headshots. Served directly, cached, CDN-able.
- **Private** — verification evidence. Signed URLs only, short TTL, every access logged, retention
  clock via `Document.delete_after`.

## Search

GeoDjango `PointField(geography=True)` with a GiST index; radius in miles via `D(mi=n)`.
Postgres full-text via `SearchVectorField` + `GinIndex`, weighted A–D (name / specialities +
synonyms / intro / services). Ranking and the banded shuffle live in
`directory/services/search.py` with the weights as module constants so tuning is one edit and
one test.

## Things deliberately not built yet

Present in the model so nothing needs migrating; unused at launch: `plan_tier`, `featured_until`,
subscriptions, availability rules. Booking, payment and client matching are **not** to be built
without prior legal review — see `docs/content-compliance.md` §9.
