# Kiam Clinic Directory — Product & Technical Specification

**Version:** 0.1 (draft for internal review)
**Owner:** Rsham — Kiam Clinic
**Status:** Draft. Requires Dr. Abbass sign-off (clinical/editorial), solicitor sign-off (listing agreement, liability, indemnity) and CQC compliance lead sign-off (independence framing, GDPR) before build completion.
**Domain:** `directory.kiamclinic.com` · own repo, own database, own deploy stack
**Stack:** Django · PostgreSQL/PostGIS · Tailwind + HTMX + Alpine — matching the main site
**Design system:** `.claude/skills/kiam-clinic-design/` copied from the main repo. Palette is read from the skill, never re-specified here.
**Read first:** `docs/multi-project-architecture.md`, then `CLAUDE.md`

---

## 1. Scope and operating model

### 1.1 What this is
A public directory of **independent** mental-health practitioners. Kiam Clinic acts as **introducer/publisher only**. Clients find a practitioner, then contact and pay that practitioner directly. Kiam does not manage appointments, does not take payment, does not supervise care.

This is Model A (membership + relay, introducer-leaning) as previously agreed. Nothing in Phase 1 should move the platform toward being a care provider or an intermediary in the payment chain.

### 1.2 Phase 1 (launch) scope
- Public search + browse + practitioner profiles
- Practitioner self-service dashboard (edit profile, upload evidence, view insights)
- Admin back office (invite, review, verify, publish, suspend, audit)
- Contact = **direct** (reveal email / phone / website). No relay form, no enquiry inbox.
- Free listing for year one
- Invite-only onboarding. No public self-registration.

### 1.3 Explicitly out of scope for Phase 1 (built for, not built)
- Subscriptions and paid Featured placement
- Booking / calendar / payment
- Public self-registration
- AI natural-language search
- Client reviews and ratings (see §10.6 — recommend permanent exclusion)

The data model and ranking layer are designed so each of these drops in without a migration of published data.

---

## 2. Architecture

Aligned to `docs/multi-project-architecture.md`. This is the third of three independent
codebases, with its own repo, own database, own deploy stack. Nothing is shared with the
main site or rooms except the design system, the compliance rules and the Claude working
practices.

### 2.1 Stack — matching the main site

| Layer | Choice | Note |
|---|---|---|
| Framework | **Django** (latest production-ready) | Same as main site and rooms. Not Wagtail — see 2.2. |
| Templates | Tailwind + **HTMX** + Alpine.js | Same as main site. |
| Components | Plain Django `{% include %}` partials in `templates/components/` | No component library, per the main-site decision — keeps a clean path to Django 6 native template partials. |
| DB | PostgreSQL + **PostGIS** (GeoDjango) | Radius search in miles, ordered by true distance, GiST index. `D(mi=10)` works natively. |
| Auth | Email magic link, thin custom `User` | Own user table. Role checks live only in `apps/accounts/access.py`. |
| Search | `django.contrib.postgres.search` + `pg_trgm` | Correct for hundreds–low thousands of listings. Do not add Meilisearch/Typesense until latency demands it. |
| Files | Two storages | Public: headshots. Private: verification evidence, signed URLs, access logged. |
| Geocoding | `postcodes.io` + OS Open Names | UK postcode → point, and place autocomplete. No Google Maps billing. |
| Analytics | Cookieless, post-consent | Consent cookie is set on `.kiamclinic.com` so a visitor consents once across all three sites. |
| Deploy | Own Docker Compose stack, own DB, own Sentry project | nginx routes by subdomain. |

### 2.2 Wagtail — one place it earns its keep

The main site uses Wagtail for editable content. The directory mostly doesn't need it: profiles
are structured data edited by practitioners, not CMS pages.

The exception is the `/[speciality]/[town]` landing pages, which need editorial intros the team
can write and Dr. Abbass can sign off. Two options: a small `EditorialIntro` model in the
directory app, or Wagtail for those pages only. **Recommend the small model** — Wagtail is a
heavy dependency to carry for one page type, and the team already has a review workflow here.

### 2.3 `kiam-ui`

Available and pinned:

```
kiam-ui @ git+ssh://git@github.com/rshamh/kiam-ui@v1.1.0
```

It provides the compiled design-system CSS, base template, header, footer, accessibility panel and
the core component partials. It contains no business logic and no models.

Rules: upgrade deliberately by bumping the pin and reading the changelog — never track a branch.
Do not fork or vendor its components; if something needs changing, change it in `kiam-ui` and bump.
Do not re-specify colour, type or spacing anywhere in this repo.

Phase 0's first task is to **read the installed package** and write its real API (base template
name, block names, settings keys, available partials, contrast pairings) into
`docs/design-system.md`. Nothing should be built against a guessed API.

**Directory-specific components** built here, not in `kiam-ui`: `SearchBar`, `LocationInput`,
`RadiusSelect`, `FilterSidebar`, `FilterGroup`, `PractitionerCard`, `VerifiedBadge`, `TagGroup`,
`ContactRevealPanel`, `CompletenessMeter`, `VerificationStatusPanel`.

### 2.4 Auth — separate, but shaped for later SSO

Option A from the architecture doc: own `User` table, own login. The only role spanning all three
projects is clinic admin — a handful of people — so SSO isn't worth a hard runtime dependency yet.

What this project does to keep Option B (main site as OIDC provider) cheap later: the `User` model
is **thin** (email as identifier, no business data — everything about a practitioner lives on
`Practitioner`), every role check sits in `apps/accounts/access.py`, and nothing anywhere assumes a
user id matches across projects.

Session cookies stay **host-only**. Do not set `SESSION_COOKIE_DOMAIN=.kiamclinic.com` — that is
Option C, which requires a shared user table and defeats the separation.

### 2.5 If PostGIS is unavailable
Store lat/lng as floats, prefilter with a bounding box, Haversine in SQL. Correct below ~10k rows.
PostGIS is still preferred — GeoDjango makes distance ordering and mile units trivial.

### 2.6 SEO across subdomains
Search engines treat subdomains as separate sites, so this project carries its **own**
`robots.txt`, `sitemap.xml` and `llms.txt`, emits its own JSON-LD with the shared NAP, and keeps
canonicals inside this subdomain. Cross-link deliberately: the main site's Directory nav item and
relevant service pages link here; profile and landing pages link back. That is how authority
flows.

The subdomain choice is **settled** by `docs/multi-project-architecture.md` — separation isolates
compliance risk and reinforces the independence framing. The slower SEO ramp is an accepted cost,
not an open question.

## 3. Taxonomy — four separate axes

The single biggest structural decision. "Specialities" is not one list. Directories that collapse these into one tag cloud become unusable within a hundred listings. Kiam should model **four independent taxonomies**:

| Axis | Answers | Example |
|---|---|---|
| **Profession** | *What are they?* | Consultant Psychiatrist, Clinical Psychologist, BACP Counsellor |
| **Speciality** (2-level: category → speciality) | *What problem do they treat?* | Neurodevelopmental → Adult ADHD assessment |
| **Approach / modality** | *How do they work?* | CBT, EMDR, Schema Therapy |
| **Client group** | *Who do they see?* | Adults, Adolescents (12–17), Couples |

Plus flat controlled vocabularies for: languages, funding/insurance accepted, accessibility features, session formats.

The intake form's Section D ("List the areas you work with — free text") should be replaced with structured pickers against these lists in the dashboard. Free text on a directory kills filtering and creates ASA exposure. Keep one free-text "other" field routed to admin as a taxonomy-request queue.

Full seed list: `src/data/taxonomy.ts` (18 speciality categories, ~150 specialities, ~35 approaches, 9 client groups).

**Editorial constraint baked into the taxonomy:** no prescription-only medication is named anywhere in the public vocabulary. The permitted terms are "Medication management", "Prescribing", "Titration and review", "Shared care". This satisfies UK ASA/CAP rules on POM advertising.

---

## 4. Data model

Full Prisma schema: `prisma/schema.prisma`. Key modelling decisions:

**Practitioners have many practice locations, not one address.** A therapist may work Tuesdays in Epsom and Thursdays in Guildford. Search must match against *any* of their locations. This is the single most common data-model mistake in directories and it's very expensive to retrofit.

**Online delivery is a separate axis from location.** A practitioner can be `offersOnline = true` with national coverage and no physical address at all. Search must union "within X miles" with "offers online" when the user allows it.

**Publication state is a state machine, not a boolean:**
`DRAFT → SUBMITTED → IN_REVIEW → (CHANGES_REQUESTED | APPROVED) → PUBLISHED → (SUSPENDED | UNPUBLISHED | REMOVED)`

Edits to a *published* profile: safe fields (bio, availability, photo) publish immediately; **controlled fields** (name, credentials, profession, registration numbers, client groups) re-enter review. Which fields sit in which bucket is a compliance decision, not a technical one.

**Verification is a set of dated records, not a flag.** Each of registration, qualification, insurance, ID and DBS is verified separately with its own `expiresAt`. The `Verified` badge is *computed*: all required checks present, none expired. Insurance lapses annually — the badge must lapse with it automatically, with 60/30/7-day dashboard and email nudges.

**Consent is versioned and evidenced.** `ConsentRecord` stores which terms version was accepted, when, and from which IP. Consent under UK GDPR is withdrawable, so the dashboard needs a genuine one-click "Unpublish my listing" — not "email us", which is what the current paper form says. That wording should change.

**Analytics are aggregated daily counters, never raw visitor rows.** Profile views, search impressions, contact reveals, website clicks. This is what will justify the subscription in year two, so start collecting from day one.

---

## 5. Search and ranking

### 5.1 Query model
Two inputs on the home page: free text ("what or who") and location ("where"), exactly as specified. Location resolves via postcode or place name to lat/lng. Radius defaults to **10 miles**, user-adjustable: 1 / 3 / 5 / 10 / 15 / 25 / 50 / Nationwide.

### 5.2 Result set
```
WHERE published
  AND ( ST_DWithin(location.geo, :point, :radiusMeters)   -- in-person in range
        OR (offersOnline AND :includeOnline) )            -- or available online
  AND <all active facet filters>
```

### 5.3 Ranking
Ordered tiers, then score within tier:

1. **Featured** (paid — Phase 2, always visually labelled "Featured", capped at 3 per page)
2. **Everyone else**, scored:

```
score = 0.35 · proximity        (1 at 0 miles → 0 at radius edge; online-only = 0.5)
      + 0.25 · textRelevance    (ts_rank over name, specialities, bio)
      + 0.15 · profileCompleteness
      + 0.10 · verified
      + 0.10 · acceptingNewClients
      + 0.05 · recentActivity   (logged in / updated within 90 days)
```

**Fairness note:** pure distance ordering means the same five practitioners sit at the top forever, which harms both users and the future subscription pitch. Apply a **seeded shuffle within score bands** (bands of ~0.05), seeded per session so pagination stays stable. Same trick powers the home-page grid — seed by date so it rotates daily and stays cacheable.

### 5.4 Filter sidebar (left, per your spec, plus additions)
Location + radius · Speciality (2-level tree) · Profession · Approach · Client group · Language · Delivery (in-person / online) · Gender · **Accepting new clients** · **Typical wait time** · **Evening & weekend** · **Fee range** · **Funding accepted** (self-pay, insurers, NHS Right to Choose, EAP, sliding scale) · **Prescriber** · **Verified only** · **Accessibility** (step-free, parking, hearing loop) · Years of experience · Session format (individual / couples / family / group)

The four bolded additions are the ones users actually convert on. "Accepting new clients" is the highest-value filter in any therapist directory and costs almost nothing to implement.

### 5.5 Facet URLs and SEO
Filtered URLs must be `noindex, follow` with a canonical to the base search page, otherwise you generate millions of thin permutations. Curated landing pages (§7) are the indexable surface, not the facet engine.

---

## 6. Page inventory

**Public**
- `/` — hero search (query + location side by side), radius control, rotating grid of 12 practitioners, trust strip, independence notice
- `/search` — results + left filter sidebar + optional map toggle
- `/p/[slug]` — practitioner profile
- `/[speciality]/[town]` — curated SEO landing pages (see rule below)
- `/specialities`, `/locations` — browse indexes
- `/for-practitioners`, `/join`, `/how-verification-works`
- `/about`, `/terms`, `/privacy`, `/cookies`, `/accessibility`, `/report-a-concern`

**Practitioner dashboard** (`/dashboard`)
- Overview (status, completeness meter, verification expiry warnings, insights snapshot)
- Profile · Locations · Specialities & approaches · Credentials & documents · Availability · Fees & funding · Insights · Account & security · **Unpublish / remove listing**

**Admin** (`/admin`)
- Invite queue · Review queue · Verification workbench · Practitioners · Taxonomy manager · Suspensions · Audit log · Reports

**Landing-page generation rule:** only generate `/[speciality]/[town]` where **≥3 published practitioners match** and a unique editorial intro exists. Auto-generated thin pages are treated by Google as doorway pages and can damage the whole subdomain. The editorial intros are clinical content and need Dr. Abbass sign-off.

---

## 7. Profile page structure

Mapped directly from the intake form, plus additions:

| Form section | Profile element |
|---|---|
| A — Personal & professional | Name, credentials, profession, headshot, pronouns (optional), gender |
| B — Contact | Contact reveal buttons (email / phone / website), locations |
| C — Service delivery | In-person / online / both badges |
| D — Specialities | Structured tag groups: specialities, approaches, client groups |
| E — About you | Intro paragraph |
| F — Services & practice | Approach and session detail |
| G — Qualifications | Qualifications table; registrations shown with body + number + *verified* tick |
| H — Documents | Not published. Drives the Verified badge only. |
| I — Consent | Not published. Stored as ConsentRecord. |
| *(new)* | Languages, fees & funding, availability summary, accepting-new-clients status, accessibility, last-updated date |

**Mandatory on every profile and every result card:** the independence notice already drafted on the form — *"Practitioners listed in the Kiam Clinic Directory are independent professionals. They are not employed by, or part of the clinical team at, Kiam Clinic. Clients arrange appointments and payment directly with the practitioner."* On result cards this can be a footer strip rather than per-card.

**On contact reveal:** show a short interstitial confirming the client is contacting an independent practitioner, not Kiam Clinic. This is the single clearest mitigation against the "I thought they were the clinic" complaint, and it also gives you the contact-reveal event for analytics.

---

## 8. Verification

### 8.1 The badge — DECIDED
Shipped as a **freshness claim**: *"Credentials checked 12 July 2026"*, computed from dated `VerificationCheck` rows and lapsing automatically when the earliest required check expires.

Rules:
- `isVerified`, `credentialsCheckedAt` and `verificationExpiresAt` are **computed only** (`recomputeVerification()`). No admin toggle exists, and none should be added.
- The displayed date is the **oldest** of the required checks — the badge is only as fresh as its weakest element.
- Public copy must state what the badge does and does not mean: documents checked and in date. **Not** a competence, quality or outcome claim. One line on the profile linking to `/how-verification-works`.
- Reminders at 60 / 30 / 7 / 0 days before expiry, dashboard banner plus email.

### 8.2 Required evidence
| Check | Required for | Expiry |
|---|---|---|
| Regulator/professional body registration | All | Per register renewal |
| Qualification proof | All | None |
| Professional indemnity insurance | All | Annual — **must** auto-lapse the badge |
| Photo ID | All | None |
| **Enhanced DBS** | **Anyone tagged with a child or adolescent client group** | 3 years, or subscribe to DBS Update Service |
| Prescriber status | Anyone tagged "prescribing" | Per register |

The DBS requirement is not currently on the intake form. It should be added.

### 8.2.1 Provisional listings — DECIDED
A practitioner may go live before their enhanced DBS returns, but **only for adult work**. "Provisional" gates the *scope of the listing*, not the *rigour of the check*.

`Practitioner.minorWorkStatus`:

| State | Meaning | Public effect |
|---|---|---|
| `NOT_APPLICABLE` | No under-18 client groups selected | — |
| `PROVISIONAL` | DBS pending, admin granted a window | Listing live. Under-18 client groups **hidden** from the profile and **excluded** from any search filtered to children or adolescents. Dashboard shows a countdown. |
| `CLEARED` | Enhanced DBS verified and in date | Under-18 groups visible and searchable |
| `BLOCKED` | Window elapsed, DBS rejected, or DBS expired | Under-18 groups locked; admin queue alert |

Implementation constraints:
- Window is **56 days** (`PROVISIONAL_WINDOW_DAYS`), granted explicitly by a named admin, audit-logged. It does not renew itself; extension is a deliberate admin act.
- The gate is applied in **two** places — the profile serialiser (`canShowMinorGroups()`) and the search query. Applying it in only one leaks: a provisional practitioner would still appear under a "children" filter.
- On lapse the practitioner is emailed and the case is raised in the admin queue. It must not fail silently.
- DBS on the Update Service should be re-checked annually; standalone certificates every 3 years.

The residual risk is that a provisional practitioner describes child work in their free-text bio, which the client-group gate doesn't catch. Mitigation: the submission lint flags child-related terms in `intro`/`services` for any practitioner not in `CLEARED`, and those profiles hold in review rather than auto-publishing.

### 8.3 Ongoing monitoring
Registers (GMC, HCPC, BACP, UKCP, BABCP, NMC) are public and searchable. Schedule a quarterly re-check — automated where a register allows lookup, manual otherwise. A struck-off practitioner sitting live on a Kiam-branded directory is the reputational worst case, and "we checked once at onboarding" is not a defensible answer.

Also needed: a written policy for what triggers immediate suspension, who can authorise it, and how fast. One-click admin suspend that pulls the profile within seconds.

---

## 9. Trust, safety and compliance

### 9.1 Positioning
Not a regulated activity — Kiam publishes information, it does not provide or arrange care. Keep it that way. Two things would change that analysis and should be avoided without legal advice: taking payment, and triaging/matching clients to practitioners.

### 9.2 UK GDPR
- **Lawful basis for publishing practitioner data:** consent, captured as a versioned `ConsentRecord`. Withdrawable → one-click unpublish.
- **Verification documents:** private bucket, encrypted at rest, access logged, restricted admin role. Retention after removal — recommend a fixed period for due-diligence defence; exact period is a solicitor question.
- **No client health data.** Direct contact means clients never type symptoms into a Kiam system. Preserve this. If a relay/enquiry form is ever added, Kiam becomes a controller of special-category data and the entire DPIA changes.
- **DPIA required** before go-live — large-scale publication of professional data plus verification document processing.
- Practitioner privacy notice, distinct from the clinic's patient privacy notice.

### 9.3 ASA / CAP
Practitioner bios are marketing communications and Kiam is the publisher — shared liability. Required controls:
- No prescription-only medicines named anywhere public (enforced by taxonomy, plus a lint on free-text fields at submission).
- No efficacy or outcome claims ("cures", "guaranteed", "%" success rates). Automated flag list at submission + human review.
- Titles must match registration — "Psychologist", "Psychiatrist" and "Counselling Psychologist" are constrained; the profession picker enforces this against the verified registration.
- Any paid Featured placement must be clearly labelled as such (Phase 2).

### 9.4 Editorial and clinical governance
All templated copy, landing-page intros, speciality descriptions and category definitions are clinical content → Dr. Abbass sign-off before publication.

### 9.5 Documents needed before go-live
1. **Practitioner Listing Agreement** — separate from and stronger than the intake form: accuracy warranty, duty to notify regulator action within a defined window, indemnity, right to suspend/remove, no-agency clause. *Solicitor.*
2. Directory terms of use (client-facing)
3. Directory privacy notice + cookie notice
4. DPIA
5. Verification policy and SOP
6. Suspension and complaints policy
7. **Confirm Kiam's own insurance covers publishing/media liability for the directory** — a standard clinical indemnity policy may not.

### 9.6 Client reviews — DECIDED: not built, at any phase
Ruled out permanently. Rationale on record: a review discloses that the reviewer is a mental-health patient (special-category data); testimonials for health services carry ASA and GMC constraints; unverifiable reviews invite manipulation and defamation exposure. The trust signal is verification, not stars.

No `Review` model exists in the schema and none should be added. If the question is reopened commercially, the answer is profile completeness and verification freshness — not ratings.

### 9.7 Accessibility
WCAG 2.2 AA. The population using this directory has a disproportionately high rate of neurodevelopmental and anxiety conditions — plain language, generous spacing, no time-limited interactions, no auto-carousels, motion-reduction respected. `kiam-ui` should already carry the contrast-safe token pairings.

### 9.8 Crisis signposting
Anyone browsing a mental-health directory may be in crisis. A persistent, non-alarming footer line — *"If you need urgent help, contact your GP, call 111, or call 999 in an emergency. Samaritans: 116 123."* Directory practitioners are not a crisis service and the site must say so.

---

## 10. Phase 2+ — designed for, not built

| Feature | What Phase 1 must already have |
|---|---|
| Subscription plans | `Plan`, `Subscription` models; `planTier` on Practitioner; feature gates read from tier |
| Featured badge / rank boost | `featuredUntil` field; ranking tier 1 already reserved; labelling rule agreed |
| Public self-registration | State machine already supports DRAFT/SUBMITTED; add signup route + stricter identity checks |
| Booking | `AvailabilitySlot` model stubbed; but note this is the change that most threatens introducer status — legal review before building |
| AI search | Keep `bio`, `services`, speciality labels as clean text; add `pgvector` column later and embed. No architectural change needed. |

---

## 11. Build order

1. Schema + taxonomy seed + admin auth
2. Admin: invite, create practitioner, verification workbench, publish
3. Public profile page + SEO scaffolding (schema.org, sitemap, canonical)
4. Search: geo + facets + ranking + filter sidebar
5. Home page: search + rotating grid
6. Practitioner dashboard
7. Insights, landing pages, polish, accessibility audit
8. DPIA, legal pack, pen test, go-live

---

## 12. Decisions

### Settled
| # | Decision | Outcome |
|---|---|---|
| 1 | Verified badge at launch | Freshness claim, computed, auto-lapsing (§8.1) |
| 2 | Enhanced DBS for under-18 work | Required — provisional listing permitted for **adult work only**, 56-day window (§8.2.1) |
| 3 | Client reviews & ratings | Permanently out, all phases (§9.6) |
| 4 | Subdomain vs subfolder | Settled by `docs/multi-project-architecture.md` — subdomain, deliberately. SEO ramp is an accepted cost. |
| 5 | Shared login across the three projects | Option A: separate auth per project. Thin `User`, checks in `apps/accounts/access.py`, OIDC later if overlap grows. |
| 6 | Component authoring | Plain `{% include %}` partials, matching the main site. |

| 7 | `kiam-ui` | Available and pinned at `v1.0.1`. Upgrade deliberately; never track a branch. |
| 8 | Translation / RTL | **Not applicable.** English only, LTR only. The main site's i18n rules are not copied here. |
| 9 | Editorial intros for landing pages | Small `EditorialIntro` model, not Wagtail (§2.2) |

### Still open
10. Which profile fields require re-review on edit? Phase 6 implements a default split (name,
    post-nominals, profession, registrations, client groups = controlled; intro, services,
    availability, photo, fees = safe). Confirm or amend — it's a compliance decision, not a
    technical one.
11. Retention period for verification documents after a listing is removed. (Solicitor.)
    `Document.delete_after` is configurable; don't pick a number in code without the answer.
12. Provisional-window extension policy: who can extend, and confirm that a second extension
    requires Dr. Abbass sign-off. Phase 2 blocks the second extension in the UI on that
    assumption.

---

## 13. Consistency check against the main repo

One thing to verify: the main repo's `docs/architecture.md` previously listed the directory as
`network.kiamclinic.com (tbc)`, status *"deferred pending legal review"*. The uploaded
`multi-project-architecture.md` supersedes both — `directory.kiamclinic.com`, active. Worth
confirming the main repo's own architecture doc has been updated to match, since Claude Code
reads it every session there.
