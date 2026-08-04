# Architecture

> **Status: intent seed.** Human-authored to describe the intended structure. Claude Code MUST
> keep it current — after each phase, update the app map, models, URLs and roles matrix to
> reflect what actually exists in the repo.

See `docs/multi-project-architecture.md` for how this sits alongside the main site and rooms.

`docs/uml/` holds the generated companion to this file: every model with its fields and
relations, and every other class with its inheritance, read out of the code rather than written
by hand. It is regenerated with `.venv/bin/python .claude/skills/uml/generate_uml.py` (the `uml`
skill), and it describes structure only — the rules that structure exists to enforce are here and
in `CLAUDE.md`.

## Apps

**Every app lives under `apps/`.** `apps/accounts/`, `apps/directory/` and so on; `config/` (the
settings, root URLconf, WSGI/ASGI), `templates/`, `static/`, `ops/` and `docs/` stay at the
repository root. `apps/` is a container package — it ships no models, no migrations and no
`AppConfig`, and `INSTALLED_APPS` names its children (`"apps.accounts"`), never it.

The app **labels** did not change and could not: Django takes a label from the last component of
the dotted path, so `apps.accounts` is still `accounts`. `AUTH_USER_MODEL = "accounts.User"`, every
migration dependency, every `ContentType` row and every `apps.get_model("directory", …)` call
resolve against the label, which is why the move needed no migration and touched no data.

Prose in the repo names modules **app-relative** — a docstring saying
`directory.services.verification` means `apps/directory/services/verification.py`. Imports,
settings paths and anything meant to be pasted into a shell are fully qualified.

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

### What exists as of Phase 6

The table above is the intent. This is the repo.

| App | Built | Empty until |
|---|---|---|
| `accounts` | `User`, `LoginToken`, `Invite`, `UserSession`, `EmailChangeRequest`, `access.py`, `backends.py`, `services/{magic_link,passwords,ratelimit,two_factor,sessions,email_change}.py`, `middleware.py`, views, admin | — |
| `directory` | full model set, `taxonomy.py`, `services/{verification,documents,lint,search_index,search,profile,metrics,images,completeness,antivirus}.py`, `signals.py`, `factories.py`, admin, the public profile view + contact reveal, `seed_taxonomy` / `verification_sweep` / `rebuild_search_index` | — |
| `search` | `services/{params,geocode}.py`, the `/search/` view and its HTMX partials, `urls.py` | — |
| `dashboard` | `views.py`, `forms.py`, `urls.py`, `templatetags/`, `services/{overview,editing,insights}.py` — the practitioner's own editors, evidence upload, insights, security and one-click unpublish | — |
| `backoffice` | invites, review queue, verification workbench, publication, concerns, audit log — `services/{invites,review,publication,concerns}.py`, views, forms | — |
| `seo` | `jsonld.py`, `sitemaps.py`, `views.py` (robots, llms.txt, `/healthz`, 404/500 handlers), the `collectstatic` override | — |
| `pages` | `nav.py` (kiam-ui chrome config), `content.py` (the static-page registry), `services/home.py`, the real home page, the eight Phase 3 static pages, `/report-a-concern/` | landing pages — Phase 7 |

**Phase 3 additions in detail.**

* `apps/directory/services/profile.py` — slug resolution (including one hop through
  `SlugRedirect`), the read-only view model, and the under-18 gate. `CHANNELS` describes the
  three contact channels *without* their values, so the profile template cannot leak one.
* `apps/directory/services/metrics.py` — `DailyMetric` increments, done with `F()` inside a
  queryset update. Counters only: no IP, no session, no user agent.
* `apps/directory/signals.py` — two more receivers, `capture_previous_slug` (pre_save) and
  `write_slug_redirect` (post_save). Split because pre_save is the last moment the old slug
  is readable and post_save is the first moment the new one is known to have committed.
* `apps/pages/content.py` — one row per static page: URL, name, title, meta description,
  template, sitemap priority. `apps/pages/urls.py` and `apps/seo/sitemaps.py` both read it, so a page
  cannot be added without a description and cannot be added and forgotten by the sitemap.
* `apps/backoffice/services/concerns.py` — gained `submit()`, so one module owns a
  `ConcernReport` from arrival to closure. `pages` imports it; nothing in `backoffice`
  knows about a view.

**Phase 5 additions in detail.**

* `apps/pages/services/home.py` — the home page's three pieces: `hero()` (the context the shared
  search-bar partial reads), `grid()` (twelve published listings, rotating daily) and
  `browse_entry_points()` (by speciality category, by town). Nothing here takes a `request`,
  which is what makes "do not cache anything user-specific" true by construction.
  **What is cached is the SELECTION, not the rows and not the HTML** — twelve primary keys under
  `homepage:grid`, re-read from the live table on every render. That costs two indexed queries
  per page and buys three things: a stale cache cannot show a suspended listing (the hydration
  query re-applies `status=PUBLISHED`), compliance copy is never a day out of date, and a
  migration cannot turn Redis into a 500 the way a pickled model instance can. `homepage:browse`
  joins `backoffice.services.publication.CACHE_KEY_PATTERNS` for the same reason the grid key is
  in it.
* `apps/directory/services/images.py` — headshot renditions at 80/160/240 px, which is what finally
  gives `_practitioner_card.html` a `srcset` (deferred there since Phase 3). Deterministic names
  derived from the original's, so a replaced headshot gets new rendition names and there is
  nothing to bust. All-or-nothing and never fatal: any failure returns `""` and the card falls
  back to the plain `src`, because a `srcset` naming a rendition that does not exist is a broken
  image. Built inside the cached grid path, so twelve conversions happen once a day rather than
  on every render. Re-encoding drops EXIF, which takes the GPS coordinates out of a phone photo.
* `apps/directory/services/search.py` — `homepage_grid()` stopped being unused code, and its daily
  seed moved from the UTC date to `timezone.localdate()` so it rotates at the same moment the
  result shuffle does. `decorate_cards()` is a public seam onto `_decorate` so the grid can ask
  for the same card decoration as a results page without reaching into a private function. Two
  clauses were added at the gate review and both are about the grid being an **editorial sample**
  rather than an answer to a question: `FEATURED_CAP_PER_PAGE` applies here too (four paid
  listings had taken the first four of twelve slots, under a disclosure promising three), and
  `_ungated_minor_work_ids()` keeps listings that advertise under-18 work without a cleared DBS
  out of the selection. The second is **not** a third under-18 gate — see
  `apps/pages/tests/test_home_minors_gate.py`.
* **Town browse links centre on an outward code, not a town name.** `?near=Croydon` resolved to
  Croydon *Cambridgeshire* — `geocode.places()` takes the first OS Open Names match with no
  importance ranking, and three of ten links led to the wrong county. `?near=CR0` goes through
  `/outcodes/`, an exact lookup. A person typing a town can correct the resolved label; a link
  asserts the destination. The `county` column is no help — the demo seed has Croydon in West
  Yorkshire, and nothing lints it.

**Phase 6 additions in detail.**

* `apps/directory/services/completeness.py` — **the writer `Practitioner.completeness` never had.**
  The column has been a ranking input since Phase 1 and the home-grid gate since Phase 5, and
  nothing wrote it, so every real listing sat at 0. One writer, through a queryset `.update()` so
  the signals that call it cannot recurse; `REQUIREMENTS` produces both the score and the
  checklist, so the meter and the advice cannot disagree; weights sum to 100 and a test says so.
* `apps/dashboard/services/editing.py` — the controlled/safe split, derived from
  `review.CONTROLLED_FIELDS` rather than restated. Owns the wording of what a save just did,
  including the fact that a controlled edit keeps the listing **online** and takes the badge.
* `apps/directory/services/antivirus.py` — upload scanning with a fail-closed default (`reject`),
  a pure-socket ClamAV INSTREAM client (no new dependency), and size/type limits. `skip` is
  development-only and `prod.py` deliberately sets nothing.
* `accounts.UserSession` / `accounts.EmailChangeRequest` — signed-in devices, and an email change
  confirmed at **both** addresses. The two halves guard opposite failures: confirming only at the
  new address lets an unattended session move the account away; only at the old one lets a typo
  lock the owner out.
* `apps/backoffice/services/publication.py` — `withdraw_consent()` and `request_removal()`, each
  closing every open `ConsentRecord` and changing publication state in one transaction.

**Models:** `accounts.{User, LoginToken, Invite, UserSession, EmailChangeRequest}` plus the full `directory` set — the four
taxonomy axes (`Profession`, `SpecialityCategory`/`Speciality`, `Approach`, `ClientGroup`), the
flat vocabularies (`Language`, `FundingOption`, `SessionFormat`), `Practitioner`,
`PractitionerLocation`, `Qualification`, `Registration`, `VerificationCheck`, `Document`,
`DocumentAccessLog`, `ConsentRecord`, `ReviewRequest`, `AuditLog`, `ConcernReport`,
`SlugRedirect`, `TaxonomyRequest`, `DailyMetric`.

`apps/accounts/models.py` and `apps/accounts/access.py` are **authored elsewhere and adopted verbatim** —
`models.py` is byte-identical, and `access.py` has the Phase 0 two-factor helpers appended below
the authored block, which is unchanged. `Invite` therefore lands in Phase 0 because it is in that
file; only the issue/accept *flow* waits for Phase 2, and the admin registers it read-only until
then. The `directory` models (also authored) land in Phase 1.

**URLs:**

| Path | Name | Notes |
|---|---|---|
| `/` | `pages:home` | hero search (a plain GET to `/search/`), a daily-rotating grid of twelve, the independence notice above the grid, browse entry points, cross-links to kiamclinic.com. `WebSite` + `Organization` JSON-LD. Indexable, priority 1.0 in the sitemap. The grid and the browse lists are cached for 24 h and busted by every publication change |
| `/p/<slug>/` | `directory:profile` | public profile. PUBLISHED only — everything else 404s, including a suspension. Resolves one `SlugRedirect` hop with a 301 |
| `/p/<slug>/contact/<channel>/` | `directory:contact_reveal` | **POST only.** `channel` ∈ `email` \| `phone` \| `website`, fixed by a URL converter. Returns a partial to HTMX and a whole page otherwise. `noindex, nofollow` + `X-Robots-Tag`. Rate limited per IP |
| `/about/` | `pages:about` | |
| `/how-verification-works/` | `pages:how_verification_works` | what the badge does and does not mean. `TODO(sign-off)` — Dr. Abbass |
| `/for-practitioners/` | `pages:for_practitioners` | |
| `/accessibility/` | `pages:accessibility` | |
| `/terms/` `/privacy/` `/cookies/` | `pages:terms` etc. | placeholders. `TODO(sign-off)` — solicitor |
| `/report-a-concern/` | `pages:report_concern` | writes a `ConcernReport`. Accepts `?listing=<slug>` to prefill |
| `/search/` | `search:search` | the search page. Whole page on a normal GET, `partials/_results.html` when `request.htmx` — **same URL**, so there is no fragment-only address to index. `noindex, follow` on any faceted query string; the canonical is the base template's request-derived one, which excludes the query string and so already points at the bare path. From Phase 5c the results are a **register** — one row per practitioner with a rail — and the next page **appends** (`hx-select` + `beforeend` + `hx-select-oob`), degrading to a plain `?page=2` link with script off |
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
| `/dashboard/…` | `dashboard:*` | The practitioner's own listing — overview, six editors, evidence upload, insights, account & security, unpublish and removal. `can_use_dashboard` + `owns_practitioner` on every view, `noindex`, `never_cache`, `Disallow`-ed. **No route names a practitioner**; the listing comes from the session |
| `/accounts/email/confirm/<token>/` | `accounts:email_change_confirm` | One half of an email change. Takes no session — the link sent to the NEW address goes to somebody who may not be signed in anywhere, which is the case it exists for |
| `/backoffice/…` | `backoffice:*` | the whole Phase 2 staff area — invites, review queue, verification workbench, suspend, concerns, audit log. Every view carries an `accounts.access` predicate, the 2FA middleware gates the prefix, and `robots.txt` disallows it. See `apps/backoffice/urls.py` |
| `/backoffice/practitioners/<pk>/verification/verify-all/` | `backoffice:verification_verify_all` | POST. Records a verification decision against every required check at once. `can_view_evidence` only — the role that decides evidence is satisfactory must be the role allowed to look at it. Routed **before** the `<check_type>` route, which would otherwise swallow it |
| `/<ADMIN_URL_PATH>/` | Django admin | default `staff-console/`, not `/admin/` |

**Management commands:** `seed_taxonomy` (idempotent vocabulary load),
`verification_sweep` (nightly, 03:00), `rebuild_search_index` (nightly, 03:30),
`collectstatic` (overridden — see `apps/seo/management/commands/`). Schedule in `ops/crontab`.

**Migrations:** `directory.0001_extensions` creates `postgis` and `pg_trgm` and is kept separate
so a restricted-role deployment can `--fake` just that one; `0002_initial` is the model set,
including the GIN index on `Practitioner.search_vector` and the GiST index on
`PractitionerLocation.geo`.

**Roles:** all four exist as `accounts.models.User.Role`, and every predicate lives in
`apps/accounts/access.py`. Two things the matrix below does not spell out:

* `can_manage_taxonomy` is **`admin` + `superadmin` only** — narrower than `is_staff_role`. A
  verifier checks documents; they do not curate the vocabulary.
* **Two-factor is not a capability predicate.** `access.py` answers "does this role have this
  permission"; `apps/accounts/middleware.py` answers "is this session fully authenticated", and
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

All predicates live in `apps/accounts/access.py`. Views and templates never inspect `user.role`
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
`apps/directory/services/search.py` with the weights as module constants so tuning is one edit and
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

Phase 5c added two things to the same module and both are about cost:

* **`facet_counts(params)`** — five queries for the whole sidebar, each group counted with its
  OWN selection cleared so ticking one option does not zero its siblings. The under-18 gate is
  re-applied by hand, because clearing the speciality selection also clears what
  `_requests_minor_work` reads.
* **`_card_facts()`** — the monogram initials, the fee line, the status note, the languages and
  the town of the **public** address the distance was measured to, in two queries for the page.
  `search.services.params.facets_with_counts()` merges the counts onto a **copy** of the cached
  vocabulary; writing them onto the cached object would serve one visitor's numbers to everybody
  with no shape change for `FACET_CACHE_VERSION` to catch.

Geocoding is `apps/search/services/geocode.py` — postcodes.io for postcode → point and its `/places`
endpoint (OS Open Names data) for autocomplete, keyless, aggressively cached in Redis, and
returning `None` on every failure so search runs without a location rather than erroring.

## Things deliberately not built yet

Present in the model so nothing needs migrating; unused at launch: `plan_tier`, `featured_until`,
subscriptions, availability rules. Booking, payment and client matching are **not** to be built
without prior legal review — see `docs/content-compliance.md` §9.
