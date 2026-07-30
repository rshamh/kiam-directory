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

### What exists as of Phase 4

The table above is the intent. This is the repo.

| App | Built | Empty until |
|---|---|---|
| `accounts` | `User`, `LoginToken`, `Invite`, `access.py`, `backends.py`, `services/{magic_link,passwords,ratelimit,two_factor}.py`, `middleware.py`, views, admin | — |
| `directory` | full model set, `taxonomy.py`, `services/{verification,documents,lint,search_index,search,profile,metrics}.py`, `signals.py`, `factories.py`, admin, the public profile view + contact reveal, `seed_taxonomy` / `verification_sweep` / `rebuild_search_index` | — |
| `search` | `services/{params,geocode}.py`, the `/search/` view and its HTMX partials, `urls.py` | — |
| `dashboard` | app config only | Phase 6 |
| `backoffice` | invites, review queue, verification workbench, publication, concerns, audit log — `services/{invites,review,publication,concerns}.py`, views, forms | — |
| `seo` | `jsonld.py`, `sitemaps.py`, `views.py` (robots, llms.txt, `/healthz`, 404/500 handlers), the `collectstatic` override | — |
| `pages` | `nav.py` (kiam-ui chrome config), `content.py` (the static-page registry), placeholder home, the eight Phase 3 static pages, `/report-a-concern/` | the real home — Phase 5; landing pages — Phase 7 |

**Phase 3 additions in detail.**

* `directory/services/profile.py` — slug resolution (including one hop through
  `SlugRedirect`), the read-only view model, and the under-18 gate. `CHANNELS` describes the
  three contact channels *without* their values, so the profile template cannot leak one.
* `directory/services/metrics.py` — `DailyMetric` increments, done with `F()` inside a
  queryset update. Counters only: no IP, no session, no user agent.
* `directory/signals.py` — two more receivers, `capture_previous_slug` (pre_save) and
  `write_slug_redirect` (post_save). Split because pre_save is the last moment the old slug
  is readable and post_save is the first moment the new one is known to have committed.
* `pages/content.py` — one row per static page: URL, name, title, meta description,
  template, sitemap priority. `pages/urls.py` and `seo/sitemaps.py` both read it, so a page
  cannot be added without a description and cannot be added and forgotten by the sitemap.
* `backoffice/services/concerns.py` — gained `submit()`, so one module owns a
  `ConcernReport` from arrival to closure. `pages` imports it; nothing in `backoffice`
  knows about a view.

**Models:** `accounts.{User, LoginToken, Invite}` plus the full `directory` set — the four
taxonomy axes (`Profession`, `SpecialityCategory`/`Speciality`, `Approach`, `ClientGroup`), the
flat vocabularies (`Language`, `FundingOption`, `SessionFormat`), `Practitioner`,
`PractitionerLocation`, `Qualification`, `Registration`, `VerificationCheck`, `Document`,
`DocumentAccessLog`, `ConsentRecord`, `ReviewRequest`, `AuditLog`, `ConcernReport`,
`SlugRedirect`, `TaxonomyRequest`, `DailyMetric`.

`accounts/models.py` and `accounts/access.py` are **authored elsewhere and adopted verbatim** —
`models.py` is byte-identical, and `access.py` has the Phase 0 two-factor helpers appended below
the authored block, which is unchanged. `Invite` therefore lands in Phase 0 because it is in that
file; only the issue/accept *flow* waits for Phase 2, and the admin registers it read-only until
then. The `directory` models (also authored) land in Phase 1.

**URLs:**

| Path | Name | Notes |
|---|---|---|
| `/` | `pages:home` | placeholder; the real home is Phase 5 |
| `/p/<slug>/` | `directory:profile` | public profile. PUBLISHED only — everything else 404s, including a suspension. Resolves one `SlugRedirect` hop with a 301 |
| `/p/<slug>/contact/<channel>/` | `directory:contact_reveal` | **POST only.** `channel` ∈ `email` \| `phone` \| `website`, fixed by a URL converter. Returns a partial to HTMX and a whole page otherwise. `noindex, nofollow` + `X-Robots-Tag`. Rate limited per IP |
| `/about/` | `pages:about` | |
| `/how-verification-works/` | `pages:how_verification_works` | what the badge does and does not mean. `TODO(sign-off)` — Dr. Abbass |
| `/for-practitioners/` | `pages:for_practitioners` | |
| `/accessibility/` | `pages:accessibility` | |
| `/terms/` `/privacy/` `/cookies/` | `pages:terms` etc. | placeholders. `TODO(sign-off)` — solicitor |
| `/report-a-concern/` | `pages:report_concern` | writes a `ConcernReport`. Accepts `?listing=<slug>` to prefill |
| `/search/` | `search:search` | the search page. Whole page on a normal GET, `partials/_results.html` when `request.htmx` — **same URL**, so there is no fragment-only address to index. `noindex, follow` on any faceted query string; the canonical is the base template's request-derived one, which excludes the query string and so already points at the bare path |
| `/search/places/` | `search:place_suggestions` | `<option>` elements for the location field's native `<datalist>`. No links, so nothing to crawl |
| `/robots.txt` | `seo:robots` | this subdomain's own. Deliberately does **not** disallow `/search/` — the facet URLs must stay crawlable for their `noindex` to be read |
| `/llms.txt` | `seo:llms` | this subdomain's own |
| `/sitemap.xml` | — | `django.contrib.sitemaps`, `seo.sitemaps.SITEMAPS` — `static` (the `pages` registry) and `practitioners` (PUBLISHED only) |
| `/healthz` | `seo:healthz` | app + DB + Redis; 503 when degraded |
| `/accounts/login/` | `accounts:login` | magic-link request. **No signup route exists** |
| `/accounts/login/check-your-email/` | `accounts:login_sent` | |
| `/accounts/login/<token>/` | `accounts:magic_link_consume` | token in the path, not a query string |
| `/accounts/logout/` | `accounts:logout` | POST only |
| `/accounts/two-factor/set-up/` | `accounts:two_factor_setup` | |
| `/accounts/two-factor/set-up/qr.svg` | `accounts:two_factor_qr` | generated locally; the secret never leaves the host |
| `/accounts/two-factor/` | `accounts:two_factor_verify` | |
| `/backoffice/…` | `backoffice:*` | the whole Phase 2 staff area — invites, review queue, verification workbench, suspend, concerns, audit log. Every view carries an `accounts.access` predicate, the 2FA middleware gates the prefix, and `robots.txt` disallows it. See `backoffice/urls.py` |
| `/backoffice/practitioners/<pk>/verification/verify-all/` | `backoffice:verification_verify_all` | POST. Records a verification decision against every required check at once. `can_view_evidence` only — the role that decides evidence is satisfactory must be the role allowed to look at it. Routed **before** the `<check_type>` route, which would otherwise swallow it |
| `/<ADMIN_URL_PATH>/` | Django admin | default `staff-console/`, not `/admin/` |

**Management commands:** `seed_taxonomy` (idempotent vocabulary load),
`verification_sweep` (nightly, 03:00), `rebuild_search_index` (nightly, 03:30),
`collectstatic` (overridden — see `seo/management/commands/`). Schedule in `ops/crontab`.

**Migrations:** `directory.0001_extensions` creates `postgis` and `pg_trgm` and is kept separate
so a restricted-role deployment can `--fake` just that one; `0002_initial` is the model set,
including the GIN index on `Practitioner.search_vector` and the GiST index on
`PractitionerLocation.geo`.

**Roles:** all four exist as `accounts.models.User.Role`, and every predicate lives in
`accounts/access.py`. Two things the matrix below does not spell out:

* `can_manage_taxonomy` is **`admin` + `superadmin` only** — narrower than `is_staff_role`. A
  verifier checks documents; they do not curate the vocabulary.
* **Two-factor is not a capability predicate.** `access.py` answers "does this role have this
  permission"; `accounts/middleware.py` answers "is this session fully authenticated", and
  refuses to let an unverified staff session reach any page but the challenge. Keeping them apart
  means a future predicate cannot forget to check 2FA — a whitelist, not a checklist.

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

   Re-entering review does **not** move the listing out of PUBLISHED. The page stays up and the
   *badge* is what is withheld — `backoffice.services.review.submit_update()` raises the review and
   `directory.services.verification.invalidate_for_changes()` reopens the checks that were made
   against whatever changed, so `recompute()` drops `is_verified` on its own. Approving the copy
   (`approve_update()`) does not restore it; re-verifying the evidence does.

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

Built at Phase 4. Four things about it are decisions rather than implementation:

* **Every constraint on a practice address is ONE `Q` against ONE relation** (`_location_filter`).
  Two `.filter()` calls on `locations` ask two independent questions and let "in range" and
  "step-free" be satisfied by different buildings.
* **An accessibility requirement implies in person**, so it switches off the "…or works online"
  fallback (`SearchParams.wants_physical_venue`).
* **The featured tier is capped per page and labelled** (`FEATURED_CAP_PER_PAGE`,
  `results_page()`), before anything is featured, because undisclosed paid ranking breaches CAP
  rules.
* **The shuffle seed is date-derived, not session-derived**, so running a search sets no cookie.
  It rides in pagination URLs and is overridable with `?seed=`.

Geocoding is `search/services/geocode.py` — postcodes.io for postcode → point and its `/places`
endpoint (OS Open Names data) for autocomplete, keyless, aggressively cached in Redis, and
returning `None` on every failure so search runs without a location rather than erroring.

## Things deliberately not built yet

Present in the model so nothing needs migrating; unused at launch: `plan_tier`, `featured_until`,
subscriptions, availability rules. Booking, payment and client matching are **not** to be built
without prior legal review — see `docs/content-compliance.md` §9.
