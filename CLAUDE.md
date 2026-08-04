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

**How an admin actually grants a badge.** The rule above is about who can *set* the field, not
about how much clicking a verifier has to do. `verification.verify_all_required()` records a
verification decision against every required check in one action — one insurance expiry date, one
confirmation box, one button in the workbench. It is not a toggle and does not become one: it
writes the same dated, expiring, per-check audit-logged rows a verifier would write one at a time,
so the badge is still derived and still lapses by itself on the date entered. **The expiry date is
required and deliberately not prefilled** — it is the thing that makes a one-click grant safe, so
it has to be copied off the certificate rather than accepted unread.

DBS is **not** in that bulk action even for a practitioner who works with under-18s. It is not one
of the badge's required checks, it is the safeguarding one, and it stays a separate deliberate act.

**A controlled edit to a live listing withdraws the badge, and the listing stays up.**
`review.submit_update()` is the entry point (`PractitionerAdmin.save_related` calls it today; the
Phase 6 dashboard will call the same function). Two things about it are the design rather than the
implementation:

* **The page is not taken down.** Pulling a live listing because somebody corrected their own
  surname would punish keeping a listing accurate. What is at risk is the badge — the claim that
  Kiam checked these details — so that is what goes.
* **The badge is not switched off, because it cannot be.**
  `verification.invalidate_for_changes()` reopens the checks that were made *against whatever
  changed* (`CHECKS_INVALIDATED_BY`), and the badge falls out of `recompute()` because a required
  check is no longer VERIFIED. A name checked against photo ID stops being a checked name the
  moment the name changes.
* **Approving the copy does not give the badge back.** `review.approve_update()` closes the review
  and changes no publication state; the badge returns when a verifier re-verifies the reopened
  checks. An admin accepting a name change must not thereby assert that somebody's photo ID
  matches the new name. `test_approving_the_wording_does_not_restore_the_badge` is the line.

`open_queue()` includes PUBLISHED for this reason — a listing whose badge has just been withdrawn
must appear in a queue, or the practitioner waits for a re-check nobody can see they are owed.

> **Both halves now exist.** The profile half is `Practitioner.can_show_minor_groups` /
> `visible_client_groups()`, called by `directory.services.profile.build()`. The search half is
> `directory.services.search.build_queryset()`, which adds
> `filter(minor_work_status=MinorWorkStatus.CLEARED)` whenever the requested client groups
> include an `is_minors=True` term. Each has its own gate test —
> `apps/directory/tests/test_profile_minor_gate.py` and `apps/directory/tests/test_search_minors_gate.py` —
> and the second one ends with a grep asserting **both** call sites are still in the source,
> because deleting either would leave the other's tests green.
>
> The trap the search half has and the profile half does not: `group=adults&group=adolescents` is
> an OR over client groups, so a `PROVISIONAL` practitioner matches the adults half. Asking about
> under-18s *at all* triggers the gate, not asking about them exclusively.

> **Why it is two places and not one.** The profile gate stops somebody who is already looking at
> a listing from seeing under-18 groups; the search gate stops the listing being offered to
> somebody who asked for a child therapist. The second is the higher-severity of the two, and
> neither substitutes for the other.
>
> **STILL OPEN, and Phase 5 raised its priority again.** Only *client groups* are gated. A
> speciality with `implies_minors=True` — "Child & adolescent ADHD assessment" — is neither gated
> on the profile nor gated in search, and a listing that tags one while selecting only adult client
> groups never requires a DBS at all (`recompute()` derives the requirement from client groups
> alone). Phase 5's compliance review found a **BLOCKED** listing publishing exactly that pill on
> the home page, unprompted, under Kiam's own claim to have checked the listing. The grid now
> declines to *select* such a listing (`search._ungated_minor_work_ids`, and see
> `apps/pages/tests/test_home_minors_gate.py` for why narrowing an editorial sample is not a third
> gate) — but that is a fail-safe on one surface, not the fix. The one-line fix is still in
> `recompute()`.
> Phase 4 makes this worse in two ways: a **free-text query** for "child adhd" matches that
> speciality through the search vector, and a **speciality facet** matches it directly — neither of
> which goes anywhere near the client-group gate. The one-line fix is in `recompute()` so all three
> surfaces move together; it is a safeguarding decision and waits for **Dr. Abbass / CQC lead**.
> `/how-verification-works/` is narrowed to claim only what is enforced in the meantime.

---

## Layout

**Every Django app lives under `apps/`.** `config/` (settings, root URLconf, WSGI/ASGI),
`templates/`, `static/`, `ops/`, `docs/`, `manage.py` and `conftest.py` stay at the repository
root. `apps/` is a container package: no models, no migrations, no `AppConfig`, and
`INSTALLED_APPS` names its children (`"apps.accounts"`), never it.

The app **labels** are unchanged, and that is not luck — Django derives a label from the last
component of the dotted path, so `apps.accounts` is still `accounts`. `AUTH_USER_MODEL`, every
migration dependency, every `ContentType` row and every `apps.get_model("directory", …)` call
resolve against the label, so the move needed **no migration** and touched no data.

Two things to know when writing code here:

* **Prose names modules app-relative.** A docstring saying `directory.services.verification` means
  the file `apps/directory/services/verification.py`. The import is
  `apps.directory.services.verification`. Import statements, settings values, `include()` targets
  and anything a developer would paste into a shell are fully qualified.
* **Never write an auth-backend path by hand.** `login(request, user, backend=…)` stores the
  string in the session and `auth.get_user()` returns `AnonymousUser` if it is not in
  `AUTHENTICATION_BACKENDS` — no exception, no log line. Both sign-in routes held a literal
  `"accounts.backends.…"`, and the move to `apps/` silently signed everybody out: link consumed,
  redirect served, next page anonymous. Import
  `apps.accounts.backends.BACKEND_PATH`, which is derived from the class.

## Stack

- Django (latest production-ready), PostgreSQL + **PostGIS** via GeoDjango, Redis cache.
- Tailwind + **HTMX** + Alpine.js.
- **`kiam-ui`** — the shared UI package, pinned:
  `kiam-ui @ git+ssh://git@github.com/rshamh/kiam-ui@v1.1.0`
- Auth: email identifier, no usernames, thin custom `User`. All role checks in
  `apps/accounts/access.py` and nowhere else. **Two sign-in routes, both live:**
  a magic link (always available, and the password-recovery path) and an
  **optional** password. Accounts are still created without a password —
  `UserManager` calls `set_unusable_password()` — and only get one if their
  owner sets it, so "no password" remains a valid permanent state.
  Password sign-in must keep the magic link's two guarantees: no account
  enumeration (one message for wrong-password / unknown-address / no-password /
  deactivated) and rate limiting per email **and per IP**. TOTP still applies to
  staff regardless of which route they came in by — a password is not a way
  round the second factor.
- Search: `django.contrib.postgres.search` + `pg_trgm`; geo via GeoDjango `PointField` + GiST.
  **Do not add Meilisearch/Typesense** until latency actually demands it.
- Geocoding: `postcodes.io` for postcode → point; OS Open Names for place autocomplete. No Google
  Maps billing.
- Analytics: cookieless, fires only after consent.

## kiam-ui

Pinned to `v1.1.0`. **Upgrade deliberately** — bump the pin, read the changelog, run the visual
check, commit as its own change. Never track a branch.

**`docs/design-system.md` already documents its real API** — base template, all 24 block names,
every `KIAM_UI` settings key, all 17 core component partials with parameters, the token families
and the documented WCAG contrast pairings. Read that rather than guessing, and **re-read the
installed package and update it whenever the pin moves**. Do not re-specify colours, type or
spacing anywhere in this repo — `kiam-ui` is the source of truth for those.

**v1.1.0's `components/directory/` vocabulary is ADOPTED, and adopting it was a redesign
rather than an upgrade.** The package's `search_bar`, `practitioner_card`, `verified_badge`,
`independence_notice`, `contact_reveal` and the rest take the "1a" direction — elevated search
bar, single-column register results, dossier profile — with different markup from the `dir-*`
components this repo built across Phases 3–5b. What was adopted is the **anatomy and the CSS**;
what stayed local is every piece of behaviour the packaged partials know nothing about: the
POST-only reveal with its focus move and per-IP limit, the two-place minors gate, the "no contact
details on a card" rule, `headshot_priority`, the badge that decides for itself whether to render,
and the independence notice as verbatim §5 copy. **Swapping markup underneath any of those is a
compliance change**, which is why the cards and the sidebar are `ds-dir-*` classes wrapping our
own markup rather than `{% include %}`s of the packaged partials.

Three consequences that are live now:

- **`.ds-dir` IS set, on the body, in `templates/base.html`.** It re-points `--focus-ring` to Sky
  Blue site-wide. Measured before it went on and again at Phase 5c: **3.12:1 on a panel, 3.06:1
  on the page** — passing WCAG 1.4.11 with nothing to spare, so anything darker than mint-200
  behind a control needs re-measuring rather than assuming. `html[data-contrast="high"]`
  out-specifies `.ds-dir` (0,1,1 vs 0,1,0) and keeps green-900 at **9.51:1**, which is the right
  outcome and an accident of specificity — `apps/pages/tests/test_chrome.py` pins it.
- **The compiled `kiam-ui.css` grew 38.5 KB → 61.2 KB** (9.8 KB gzipped, up from 7.0 KB) and is
  render-blocking on every page. Now that the `ds-dir-*` rules are actually matched this is the
  price of the components rather than dead weight; it is still small next to Gap 3's 68 KB of
  Google Fonts.
- **Local overrides of packaged rules are documented, not scattered.** §10 of
  `docs/design-system.md` is the complete list with the measurement that justified each, and two
  of them are marked **raise upstream** (`.btn-ghost`'s 1.04:1 border, and the search bar's
  focus ring on its own tint).

Its §7 "Gaps" is the list of things the package does not give us and how each is worked around.
Two matter for the next phases:

- **Fonts load from Google Fonts on every page**, before any consent decision (Gap 3). Unresolved,
  and it collides with golden rule #4 the moment analytics or consent lands.
- **`collectstatic` needs the override in `apps/seo/management/commands/`** (Gap 9), because the package
  ships its Tailwind source inside its own `static/` tree. Do not delete that command.

**Do not fork or vendor its components.** If something needs changing, raise it as a `kiam-ui`
change and bump the pin. A local override is a last resort and must be commented with why.

### Chrome decisions already made
- **`SHOW_DISCLAIMER_BAR = False`.** kiam-ui's layer-1 emergency marquee is off: this site
  provides no care, so "we are not an emergency service" asserts a clinical relationship the
  project exists to deny. Do not switch it back on.
- **Crisis signposting lives in the FOOTER**, via `pages.nav.footer` → `FOOTER["NOTE"]`. That is
  `docs/content-compliance.md` §7 and it is a separate obligation from the bar. A test asserts
  removing the bar did not take it with it.
- **`SHOW_LANGUAGE_SWITCHER = False`**, `USE_I18N = False`. English only.
- The independence notice is one partial: **`templates/components/_independence_notice.html`**,
  verbatim from §5. Profile pages, results pages and the contact-reveal interstitial must
  `{% include %}` it — never retype the sentence.

### Directory-specific components (build here, not in kiam-ui)
`SearchBar`, `LocationInput` (postcode/place autocomplete), `RadiusSelect`, `FilterSidebar`,
`FilterGroup`, `PractitionerCard`, `VerifiedBadge`, `TagGroup`, `ContactRevealPanel`,
`CompletenessMeter`, `VerificationStatusPanel`.

Built so far, in `templates/components/` and `templates/directory/`:
`_independence_notice.html` (Phase 0); from Phase 3 `_verified_badge.html`, `_tag_group.html`
and the ContactRevealPanel set — `directory/_contact_panel.html`, `_contact_button.html`,
`_contact_revealed.html`, `_contact_limited.html`; from Phase 4 `_search_bar.html` (which is the
SearchBar, LocationInput and RadiusSelect in one partial, and from Phase 5b takes a `variant` —
`hero` for the home page's segmented pill, `inline` for the row above the search results),
`_filter_sidebar.html`,
`_filter_group.html` and `_practitioner_card.html`. Their styles are the `Phase 3`, `Phase 4` and
`Phase 5` blocks at the end of `static/src/app.css`, all `dir-*` and all referencing kiam-ui
tokens.

**Phase 5 reused rather than added.** The home page's hero is `_search_bar.html` and its grid is
`_practitioner_card.html` — the same two components `/search/` uses, so the keyboard pattern, the
verification badge with its mandatory "what this does and does not mean" link, the "Paid
placement" label and the "no contact details on a card" rule are single-sourced. The card grew one
parameter, `headshot_priority`, because a list below the fold must not claim
`fetchpriority="high"`. `CompletenessMeter` and `VerificationStatusPanel` are still unbuilt —
they belong to the Phase 6 dashboard.

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

---

## What already exists (Phases 0–3)

Read this before starting a phase. Everything here is built, tested and load-bearing; the
mistakes below are ones already made once in this repo.

### Names that differ from what you would guess

| You might write | It is actually |
|---|---|
| `MagicLinkToken` | `accounts.LoginToken` (`used_at`, not `consumed_at`) |
| `document.check` | `document.verification_check` — `check` shadowed `Model.check()` and Django refused to load the app |
| `can_view_private_evidence` | `accounts.access.can_view_evidence` |
| `user.full_name` | `user.display_name` (`Practitioner.full_name` is a different field and does exist) |

### Services — the work lives here, not in views

    apps/accounts/services/    magic_link · passwords · two_factor · ratelimit
    apps/directory/services/   verification · lint · documents · search_index · profile · metrics
    apps/backoffice/services/  invites · review · publication · concerns

**Only `apps/directory/services/verification.py` may write the five computed fields.** Nothing else,
ever. `apps/directory/tests/test_admin_readonly.py` enforces this by walking the whole admin registry —
if it fails, do not add the field to `readonly_fields` to go green; something is trying to set a
computed value by hand.

### The public-visibility question has one answer

`backoffice.services.publication.is_publicly_visible(practitioner)` — a bare boolean. The Phase 3
profile view must ask exactly this and nothing more. A **suspended** profile must be
indistinguishable from one that never existed: neutral "not currently listed" page or a 404,
never an explanation. The reason is in the audit log, for staff. Publishing it would be a
defamation risk against someone who may be cleared next week.

### Evidence access

`directory.services.documents.open_evidence()` is the only door. It writes a `DocumentAccessLog`
row in the same transaction as it mints the URL, so no ordering exists in which a link is handed
out and the record is not. Private files have **no public URL** — `.url` raises. There is no
debug bypass and a test asserts `DEBUG=True` is not one.

### Submission lint

`apps/directory/services/lint.py`, run by `review.submit()`. BLOCK on `pom` and `contact`; HOLD on
`efficacy`, `child_work`, `restricted_title`. The POM blocklist is `ops/pom-dictionary.txt`
(`settings.POM_DICTIONARY_PATH`), held outside the code so it updates without a deploy — and a
**missing file raises rather than permitting everything**, because an empty blocklist silently
disables the control.

`restricted_title` holds rather than blocks because verification happens *after* review — nobody
has a verified registration at submission time by definition. §3's "cannot publish" is enforced in
`backoffice.services.review.approve()` via `blocking_publication_reasons()`, which also runs on
`publication.lift_suspension()`.

### Search index

Two signals, and the second is the one that matters: `post_save` on `Practitioner`, **and**
`m2m_changed` on `Practitioner.specialities`. An M2M change fires no save signal, so a listing
tagged "Adult ADHD assessment" would otherwise carry a vector that has never heard of ADHD —
unfindable, no error, nothing logged. `rebuild_search_index` (nightly) is the only thing that
catches a synonym added to `taxonomy.py`, because that edits the Speciality row, not the
Practitioner.

Note `SearchVector` over a joined field raises inside a queryset `.update()`, and NULL columns
NULL the whole concatenation — both already worked around in `search_index.py`.

### Test factories

`apps/directory/factories.py`. Traits: `published`, `verified`, `provisional_dbs`, `dbs_cleared`,
`online_only`, `in_person_only`, `multi_location`, `prescriber`, `locations=<n>`.

**No factory writes a verification field** — `verified=True` creates dated checks and calls
`recompute()`, as production does. Keep it that way, or every downstream assertion is testing the
factory instead of the service.

The side-effecting traits are `@factory.post_generation`, **not** `factory.Trait`: a Trait sets
values in the attributes phase, so pointing one at a post-generation hook either raises or —
worse — silently skips it. Call syntax is identical.

### Commands and schedule

`seed_taxonomy` (idempotent, never deletes — retired terms go `active=False`),
`verification_sweep` (03:00), `rebuild_search_index` (03:30), plus the `collectstatic` override.
Schedule in `ops/crontab`. A missed sweep night is a reminder nobody receives: the thresholds are
exact day buckets.

### The public profile (Phase 3)

`/p/<slug>/` → `directory.views.profile` → `apps/directory/services/profile.py`. Six things about it
are decisions, not implementation details:

* **Everything that is not PUBLISHED is a 404**, and they are all the *same* 404. A suspension,
  a draft and a slug nobody has ever used produce an identical page and an identical status
  code. `test_a_suspended_profile_is_indistinguishable_from_one_that_never_existed` compares
  the two `<main>` blocks byte for byte.
* **Contact details never enter the profile's context.** `profile.available_channels()` returns
  which channels exist; `profile.channel_value()` is a separate call the reveal view makes. The
  template therefore *cannot* leak an address, and neither can the JSON-LD — `jsonld.person()`
  omits `email`, `telephone` and `sameAs` deliberately, and a test asserts it.
* **The reveal is POST-only.** A GET URL that returns an email address is a URL a crawler
  follows and an address that is indexed within the week. Plus a per-IP limit
  (`CONTACT_REVEAL_MAX_PER_IP`) and `X-Robots-Tag`.
* **The reveal moves focus; it does not use `aria-live`.** Both would make a screen reader read
  the independence notice and the address twice. `hx-on::after-swap` on the persistent
  `.dir-contact__slot` focuses the revealed panel, which is a `role="group"` with
  `tabindex="-1"` and its own heading as an accessible name.
* **`booking_url` is not rendered.** A booking control on a Kiam-branded page asserts that Kiam
  manages the appointment — one of the three things in `docs/content-compliance.md` §9 that need
  legal review first. The field is populated; nothing displays it.
* **`BreadcrumbList` JSON-LD comes from kiam-ui's breadcrumb partial**, not from `apps/seo/jsonld.py`,
  because the partial emits it from the same `items` list it renders the visible trail from.
  `jsonld.breadcrumb_items()` builds that list with absolute URLs. Do not add a second
  `BreadcrumbList` to the graph.

**Slug redirects are two signals, and both are needed.** `capture_previous_slug` (pre_save) is
the last moment the old slug is readable; `write_slug_redirect` (post_save) is the first moment
the new one is known to have committed. Writing the row from pre_save leaves one behind for a
save that then failed. The redirect is written only when `published_at` is set — a listing that
was never live has no inbound links worth preserving — and a slug returning to use (A→B→A) has
its stale row deleted so it cannot occupy the unique `old_slug`.

**Static pages are a registry, not eight views.** `apps/pages/content.py` holds URL, name, title, meta
description, template and sitemap priority per page; `apps/pages/urls.py` and `apps/seo/sitemaps.py` both
read it. That is why a page cannot be added without a meta description and cannot be added and
forgotten by the sitemap. `/report-a-concern/` is the exception — it takes a POST — and is listed
in `FORM_PAGES`.

### What the Phase 3 gate reviews found, and what is still open

Four defects the three review subagents caught, all now fixed and covered — recorded because
each is a shape of mistake that will recur:

* **An expired enhanced DBS left `minor_work_status` at `CLEARED`.** `recompute()` was always
  right; `nightly_sweep()`'s *selection* queryset never reached the practitioner to call it. DBS
  is not in `BASE_REQUIRED`, so it contributes nothing to `verification_expires_at`, and the other
  clauses only covered `PROVISIONAL`. Dormant since Phase 1 — Phase 3 is what gave
  `minor_work_status` a public effect and turned it into a listing offering under-18 work on an
  out-of-date certificate. **A computed field is only as good as the thing that remembers to
  recompute it.**
* **The rate-limit refusal never reached the screen.** htmx 2 does not swap 4xx by default, so the
  429 partial was fetched and discarded: the button just sat there. The test asserted the
  *server* emitted `role="alert"` and passed. Server-side assertions prove nothing about the
  browser — this is the third instance of CLAUDE.md's "run the real thing" rule.
* **`og:image` concatenated `scheme://host` onto `headshot.url`.** Right under dev and test's
  `FileSystemStorage`, broken under production's S3 where `.url` is already absolute. Use
  `request.build_absolute_uri()`, which is correct under both.
* **`.prose` was used on ten templates and defined nowhere.** kiam-ui references it but never
  defines it; see `docs/design-system.md` §7 gap 10.

**Still open, and both need their gate before Phase 4:**

1. **`Speciality.implies_minors` triggers nothing.** `recompute()` derives `works_with_minors`
   from client groups alone, so a listing that tags "Child & adolescent ADHD assessment" and
   selects only adult client groups is `NOT_APPLICABLE`: **no DBS is ever required**, and it
   renders with a full badge. The model's own help text says the field "contributes to the DBS
   requirement"; nothing implements it. The fix is one line, and it is one line precisely because
   it belongs in `recompute()` — the profile, the Phase 4 search filter and the DBS requirement
   then all move together instead of forming another one-sided gate:

       works_with_minors = (
           practitioner.client_groups.filter(is_minors=True).exists()
           or practitioner.specialities.filter(implies_minors=True).exists()
       )

   `/how-verification-works/` has been narrowed to claim only what is enforced, and carries a
   `TODO(sign-off)` with this fix in it. **Dr. Abbass / CQC compliance lead.**
2. **A restricted title is checked at the transition and never again.**
   `blocking_publication_reasons()` runs at `approve()` and `lift_suspension()` only, and
   `Registration.verified` is hand-editable. Flipping it to `False` on a live listing leaves the
   protected title in the HTML and in `jobTitle`. That is exactly what the quarterly re-check is
   designed to produce, so the assertion belongs with it — **Phase 7**, alongside the re-check job.

Three smaller carry-forwards: free text on *related* models is still unlinted
(`qualification.title`/`institution`, `PractitionerLocation.label`/`days_at_site`) because the
lint reads fields off the Practitioner row; `CHILD_WORK_FLAGS` only run at submission, so a
published bio is not re-held when a DBS lapses; and every published profile is currently an
**orphan** — nothing links to one but `sitemap.xml`, because search is Phase 4 and browse is
Phase 7.

**For Phase 4 specifically:** `search_vector` already carries un-gated speciality text in band B
(`search_index.py`), so a free-text query for "child adhd" will match a `PROVISIONAL` listing
even with a correct facet filter. **The vector is not a second line of defence** — the
`minor_work_status=CLEARED` filter has to do the work on its own.

### Search (Phase 4)

`/search/` → `search.views.search` → `apps/search/services/params.py` (parsing) →
`apps/directory/services/search.py` (the query) → `apps/search/services/geocode.py` (postcode → point).

`apps/directory/services/search.py` was **adopted from the rooms repo's history**, and its module
docstring lists the five things that had to change. Four were bugs it shipped with; the tests that
pin them are grouped under `ADOPTION FIX n` headings in
`apps/directory/tests/test_search_service.py`, so a "simplification" that reintroduces one fails a test
that says why. The one worth knowing about:

* **Location predicates are ANDed onto ONE relation.** Django resolves each `.filter()` on a
  multi-valued relation against its own join, so `.filter(locations__geo__distance_lte=…)` and
  `.filter(locations__step_free_access=True)` asked *two independent questions* — satisfied by a
  practitioner with an in-range office that has steps and a step-free office twenty miles away.
  Somebody who needs step-free access would have been sent to the wrong building.
  `_location_filter()` builds one `Q`. Never add a second `.filter()` on `locations`.

Other things that will bite:

* **An accessibility filter turns off the "…or works online" fallback.** "Step-free access" cannot
  describe a video call, so asking for one asks for a building
  (`SearchParams.wants_physical_venue`). The sidebar says so, because otherwise the filter looks
  broken.
* **Distance is dropped when it falls outside the radius.** A practitioner matched *because they
  work online* still has a nearest office; printing "14.8 miles away" on a five-mile search reads
  as a broken filter. The card falls back to "Online".
* **The shuffle seed is date-derived, not a session.** "Per session" would mean a session cookie
  for every anonymous searcher, which would make `/cookies/` untrue. The seed rotates daily, is
  stable across pagination and reloads, rides in pagination URLs, and is overridable with `?seed=`.
* **The facet cache payload is versioned** (`FACET_CACHE_VERSION`). A deploy that changes the shape
  otherwise reads the old shape back out of Redis and 500s until somebody flushes it — which is
  exactly what happened while building this.
* **`FEATURED_CAP_PER_PAGE` is now used.** Nothing is featured yet; the cap and the "Paid placement"
  label exist before the first person pays, because undisclosed paid ranking breaches CAP rules
  (`docs/content-compliance.md` §6). `results_page()` splits into two querysets, because "at most
  three per page" is not something an `ORDER BY` can express.

### What the Phase 4 gate reviews found

Five defects, all fixed and covered. The first two are the shape of mistake most likely to recur:

* **A closed `<details>` did not collapse.** An author `display: grid` on a list inside it
  re-shows the content — Chrome reported `details.open === false` while the checkboxes inside
  measured `offsetWidth: 18`. The filter sidebar had **317** tabbable controls instead of 35, all
  of them in the accessibility tree, and collapsing the big group in the template had done nothing
  at all. Green suite, correct-looking markup; only measuring the rendered page showed it.
* **Four multi-line `{# … #}` comments rendered as page text**, one of them where the radius label
  should have been. `{# #}` is single-line only in Django.
* **A POM name reached `<title>` and `og:title`** — a search for a medicine name published it in
  the field chat clients read to build a link preview. `docs/content-compliance.md` §1 names meta
  titles, and this is authored markup, so the lint never sees it. The title is static now; the echo
  into the input's `value` stays, because that is how a search box works.
* **`/search/places/` was an indexable HTMX partial** — head-less HTML cannot carry a `noindex`
  meta tag, and `?near=` is unbounded, so it was one thin URL per typed prefix. `X-Robots-Tag`
  plus a `Disallow` now, and the reasoning that let it through ("no links to follow") tested the
  wrong thing.
* **`/search/` was declared indexable, in no sitemap, and linked from nowhere** — while the home
  page said search did not exist and the same page's JSON-LD advertised a `SearchAction` for it.
  Now in the nav, in the sitemap, and the home-page copy no longer contradicts it.

Plus a fail-safe safeguarding interim: **an explicitly selected `implies_minors` speciality now
triggers the under-18 gate** (`search._requests_minor_work`). Categories and free text deliberately
do not — see the open question above and the docstring for why each would be too blunt.

**HTMX and the no-JS path.** One `<form>` wraps the search bar, the sidebar *and* `#results`. Not
three forms and not `form=` attributes: with script off, somebody who has typed a location and then
ticks a filter must submit both. `#results` sits inside that form and contains no inputs, so the
form serialises only the filters. `apps/search/tests/test_search_view.py::test_search_works_completely_without_javascript`
is the one that must never be allowed to fail.

The result count is a **persistent live region outside `#results`**, updated by
`hx-swap-oob="innerHTML:#result-count"`. A live region that is itself replaced by the swap is not
announced — the same lesson as the Phase 3 contact reveal.

### The home page (Phase 5)

`/` → `pages.views.home` → `apps/pages/services/home.py`. Five things about it are decisions, not
implementation.

* **The hero search is the SAME component as `/search/`.** `components/_search_bar.html`, wrapped
  in a plain `<form method="get" action="/search/">`. The brief calls the hero the most important
  interaction on the site, and a second implementation of the query field, the location field's
  native `<datalist>`, the radius `<select>` and the always-visible submit button would be a
  second one to keep accessible. The whole thing is a plain GET with script off —
  `test_the_hero_search_works_completely_without_javascript` is this phase's equivalent of the
  search page's non-negotiable test. **Phase 5b gave the partial a `variant`** — see below; the
  two surfaces still differ by a modifier class and nothing else.
* **The cache holds primary keys, not rows and not HTML.** `homepage:grid` stores twelve ids;
  every render re-reads the rows and **re-applies `status=PUBLISHED`**. So a `bust_cache()` that
  never fires still cannot leave a suspended listing on the busiest page on the site — the
  reputational worst case in `docs/verification-policy.md`. It also means compliance copy is never
  a day stale, and that a migration cannot turn Redis into a 500 the way a pickled model instance
  can (the `FACET_CACHE_VERSION` lesson, one phase later). `homepage:browse` is in
  `publication.CACHE_KEY_PATTERNS` for the same reason.
* **The independence notice is ABOVE the grid.** The brief allowed "above the fold or immediately
  below the grid"; above is strictly better, because the sentence a visitor needs before reading
  twelve names and photographs on a Kiam-branded page cannot be below them.
* **The hero lede is short on purpose.** The first draft put the whole plain-language explanation
  there, and on a 375px viewport that was a nine-line serif paragraph between the visitor and the
  search box. It is now one sentence — still the extractable AEO answer (`docs/seo.md`) — and the
  fuller version is its own "What this directory is" section.
* **Browse entry points are counted from live listings**, never from the taxonomy, and capped at
  twelve each. They are `noindex, follow` facet URLs until Phase 7's curated landing pages replace
  them; a home page fanning out into dozens of thin permutations is the doorway pattern
  `docs/seo.md` forbids. A town needs **two** published practitioners before it is named, and
  `is_public=False` addresses are excluded — naming the town of a single home office publishes it
  by inference.

**Headshot renditions arrived here**, not in Phase 3 where the card first wanted them:
`apps/directory/services/images.py`, 80/160/240px, built inside the cached grid path so twelve
conversions happen once a day. Names are deterministic from the original's, so a replaced headshot
gets new rendition names and there is nothing to bust. It is **all-or-nothing and never fatal** —
any failure returns `""` and the card falls back to the plain `src`, because a `srcset` naming a
rendition that does not exist is a broken image. Re-encoding drops EXIF, which takes the GPS
coordinates out of a phone photo. `/search/` is unchanged: it builds no renditions, so it emits no
`srcset`.

### What the Phase 5 gate found

**Eleven defects, and the three review subagents caught seven of them.** Recorded in full because
the shapes recur, and because two of the worst were in copy *this phase wrote*.

**Four found by running the real thing** — none visible to the suite:

* **`extra={"name": ...}` in a log call raises.** `name` is a reserved `LogRecord` attribute and
  `Logger.makeRecord` raises `KeyError` on a collision — so the line meant to *record* a failure
  *became* the failure, and turned "a broken image never breaks the page" into a 500 on the home
  page. The suite could not see it: `config/settings/test.py` sets the root logger to CRITICAL, so
  `isEnabledFor` is False and `makeRecord` is never reached.
  `test_the_logging_calls_are_actually_emittable` turns logging on for exactly this.
* **Concurrent requests wrote duplicate renditions.** Django's storage never overwrites, so the
  loser of a write race got a suffixed name nothing would ever request. Measured: five concurrent
  cold-cache loads produced 111 orphans. `_write` deletes the file it lost with.
* **`.dir-steps` had no numbers.** Tailwind's preflight sets `ol { list-style: none }`, and this
  class is used where the sequence *is* the information — WCAG 1.3.1, on the home page and on
  `/two-factor/setup/` since Phase 0.
* **The footer wordmark accent measured 1.76:1.** In the chrome, on every page since Phase 0.

**Three blockers from the compliance review:**

* **The grid published child-work specialities, unprompted, for practitioners with no DBS.** Both
  real gates were intact and the card renders no client groups — this was `implies_minors`
  *specialities*, and a **BLOCKED** listing was carrying "Child & adolescent ADHD assessment" on
  the front page on five of six days simulated. Materially worse than `/search/` for one reason:
  **nobody asked.** On search that route needs a typed query or a ticked facet; here Kiam selects
  the twelve and publishes the pill under its own claim to have checked the listing.
  Fixed in `homepage_grid()` via `_ungated_minor_work_ids()`, with
  `apps/pages/tests/test_home_minors_gate.py`. **This is not a fourth gate** — see that file's
  docstring for why narrowing an editorial *sample* is a different act from gating a *query*, and
  why it leaves no asymmetry for a later change to break.
* **The page claimed every listing was credential-checked before publication.** It is not:
  `blocking_publication_reasons()` blocks only a restricted title with no verified registration,
  `is_verified` is a separate computed field, and the badge-withdrawal design deliberately keeps a
  listing **up**. Nine of twenty-eight published listings carry no badge. Every verification claim
  on the page is now conditional on the badge, and the page says plainly that absence means
  something — because `_verified_badge.html` renders *nothing* for an unverified listing, so a
  visitor cannot read the absence unaided. The same over-claim is in
  `for_practitioners.html` and `how_verification_works.html`'s table caption: **not fixed here**,
  flagged for Dr. Abbass, because it is systemic copy rather than a home-page typo.
* **13 published listings hold a restricted title with no verified registration.** Demo data:
  `seed_demo` writes `status=PUBLISHED` directly, bypassing `approve()` and therefore
  `blocking_publication_reasons()`. **Not fixed here** — it is dev-data, and the real answer is
  Phase 7's continuous re-check (open item 2). Flagged, because a demo dataset that cannot fail
  the gate it is used to review is worse than no dataset.

**Two blockers from the accessibility review**, both measured, neither visible to Lighthouse or
axe (which test neither reflow nor focus-indicator contrast):

* **The grid did not reflow at 320px — WCAG 1.4.10.** A `minmax()` min track cannot shrink below
  its floor, so `minmax(19rem, 1fr)` forced 304px cards into a 264px container:
  `scrollWidth: 340` against `clientWidth: 320`, the whole grid sliced off at the right edge.
  320px **is** the requirement (1280px at 400% zoom), and it broke below ~336px — which is
  precisely why the 375px check passed. `minmax(min(19rem, 100%), 1fr)`.
* **The focus ring was invisible on the CTA banner and in the footer — WCAG 1.4.11.**
  `--focus-ring` is green-600 and `.ds-cta` is a green-800→green-600 gradient, so the outline was
  drawn *on* the gradient in the same colour as one end of it: **1.00:1**. The footer gave 1.76:1.
  And `html[data-contrast="high"]` made both **worse**, because it re-points `--focus-ring` at the
  darker green-900 — 1.03:1 in the footer, invisible for the one user who asked for more contrast.
  Design-system §5 claims the package swaps in a light ring on deep surfaces; it does, for
  `.disclaimer-bar` only, which this site switches off. Gaps 19–21.

**Two the reviews caught in code this phase wrote, which are worth more than the blockers:**

* **`CACHE_VERSION` was not bumped, and this module invented the rule.** `count_label` was added
  to the browse payload and `FEATURED_CAP_PER_PAGE` to the grid selection; the shape check passed,
  the old payload was served, and the live page rendered browse counts **with no unit at all**
  next to **four** "Paid placement" cards under a sentence promising no more than three. Both
  fixes were correct and both were invisible for as long as the entry lived. The constant's
  docstring now says **shape or meaning** — a selection rule is as much part of a cached payload
  as its keys are.
* **Three of ten town links led to the wrong county.** `?near=Croydon` resolved to Croydon,
  *Cambridgeshire*; Brighton to *Cornwall*; Guildford to *Pembrokeshire* — `geocode.places()`
  takes the first OS Open Names match with no importance ranking. A person typing a town sees the
  resolved label and can correct it; **a link asserts the destination**, so Phase 5 turned a
  Phase 4 weakness into a defect. Town links now centre on an outward code (`?near=CR0`), which
  goes through `/outcodes/` — an exact lookup, not a prefix search — and carry
  `delivery=in_person`, because "By where they work" should not OR in everyone who works online.
  The `county` column is no help: the demo seed has Croydon in West Yorkshire.

Also from the reviews and applied: `/` and `/search/` shipped byte-identical `<title>`, `og:title`
and `<h1>` (the search page moved); the hero lede was a subject-less fragment that named no entity;
the CTA advertised a message relay `apps/directory/views.py` says needs legal review before anyone
builds one; the browse counts described neither of the two different things they count;
`alt=" Marcus Osei"` carried a leading space; the cross-links cost a redirect hop each; `llms.txt`
published an HTML entity into a `text/plain` file and told crawlers browsing did not exist while
the page browsed.

**Measured Core Web Vitals** (Lighthouse 13.4.1, production-like local server — `DEBUG` off so
WhiteNoise serves the compressed static a visitor gets; 3 mobile runs, medians):

| | Mobile, Slow 4G (1,638 Kbps, 150 ms RTT, 4× CPU) | Desktop |
|---|---|---|
| Performance | 89 | 99 |
| LCP | 3.21 s | 0.78 s |
| CLS | 0.001 | 0.002 |
| TBT | 0 ms | 0 ms |
| FCP | 2.61 s | 0.69 s |
| Accessibility | 100 | 100 |
| Best practices | 100 | 96 |
| Weight | 199 KiB / 13 requests | 209 KiB / 19 requests |

LCP is the only metric outside "good" and **it is not this page's markup**: 1,510 ms of it is
render-blocking stylesheets, of which Google Fonts is 796 ms and 68 KiB. Blocking that origin takes
mobile LCP to **1.84 s** and the score to **98**. That is design-system **gap 3** with a number on
it, and it collides with golden rule #4 — the fix is self-hosting the two families upstream in
`kiam-ui`, not here. The next item after fonts is the **HTML document, 81 KiB uncompressed**:
nothing in the stack gzips a response body (`GZipMiddleware` is deliberately absent — BREACH), so
that belongs to the reverse proxy or CDN at Phase 7 launch prep.

**Lighthouse and axe both scored the page 100 on accessibility while it had two AA failures.**
Worth remembering before the Phase 7 audit: neither tests reflow at 320px, focus-indicator
contrast, nor 2.5.8's spacing exception, and those are where both blockers lived.

### The hero search bar, rebuilt (Phase 5b)

Requested as "more authentic and professional, especially the search box", against a
segmented-pill reference. What it replaced measured **576px on a 375×812 phone** — 71% of the
viewport, Search button below the fold — with 55 words of grey help text inside the box. It
worked; it read as an admin form on a homepage. It is **281px** now, and the whole bar including
its button is above the fold on that phone.

Five things are decisions:

* **One markup, two layouts.** `_search_bar.html` takes `variant="hero"`; the elements, ids, names
  and DOM order are identical either way and only CSS differs. The single-implementation rule
  exists so there is one keyboard pattern to keep accessible, and a "hero version" of the search
  box would have quietly ended that.
* **The CELL carries the boundary, not the input.** One surface, one border, hairline dividers,
  and every input inside loses its own border and background. That single change is most of why it
  stops reading as a form. Two consequences worth knowing: the boundary that has to meet WCAG
  1.4.11 is now the bar and the dividers (`--control-border`, 3.21:1 and 3.28:1 measured), and the
  focus ring is the only thing identifying *which* control has focus, so it is pulled tight rather
  than left to the packaged style.
* **The radius moved inside the location cell.** "Where" and "how far" are one question; asking
  them as two produced a label — "Within (distance from you)" — that wrapped to three lines and was
  wider than its own select. The visible word is now "Within" with the rest of the accessible name
  in `.sr-only`, which keeps 2.4.6 and satisfies 2.5.3 (the visible text is the first word of the
  accessible name). `/search/` got the same fix for free.
* **The hint is one line, revealed by `:focus-within` in CSS.** Not script: a JS version needs an
  inline script to avoid a flash of the un-enhanced layout, and this is the component whose whole
  point is that it works with scripting off. Every hint is still its field's `aria-describedby`
  target, so screen-reader behaviour is byte-identical to when all three were stacked; the visible
  resting line is `aria-hidden` and there is **no `aria-live`**, because announcing a description
  that is already exposed is the Phase 3 contact-reveal mistake in a new place.
* **The submit button keeps the word "Search".** The reference uses an icon-only circle. With
  scripting off this button is the only way to run a search, and for an audience with a high rate
  of anxiety and neurodevelopmental conditions an icon anyone has to interpret is a worse control
  than a word anyone can read.

**Four things were only findable by rendering it**, which is the recurring lesson and now has four
more instances:

* **`background: transparent` erased the select's chevron.** The shorthand resets
  `background-image`, and kiam-ui draws the chevron with one — so `<select>` rendered as plain text
  with no affordance that it opened anything. Measured `backgroundImage: "none"`.
* **`position: relative` on a cell put its own hint on top of the location input.** Relative
  positioning makes the cell the containing block for everything absolute inside it, including the
  hint that is supposed to be anchored to the foot of the bar. The divider is a `background-image`
  gradient now, which needs no positioning context.
* **The reserved hint row was one line too short**, so on a phone the tallest hint grew *upwards*
  and rendered underneath the Search button. Fixed twice over: the row is anchored to its **top**,
  so any overflow goes downward past the bar's edge where it cannot cover a control, and its height
  is `calc(var(--text-caption) * 1.35 * 3)` rather than a fixed `rem` — because at a fixed size it
  spilled 8px under `html[data-font-size="larger"]`, i.e. it degraded in exactly the mode somebody
  turns on because they are struggling to read. Same shape as Phase 5's focus ring getting *worse*
  in high contrast.
* **The gradient's stops are a contrast result, not a taste one.** With the first draft's stops the
  backdrop behind the bar computed to #EDF4F2, where `--control-border` is 2.96:1 and
  `--border-default` is 2.79:1 — both under 1.4.11's 3:1, and invisible to every automated checker
  because none samples a gradient at an element's own y-offset. The stops reach `--surface-page` by
  52%, so every control in the hero sits on a flat known colour.

**The chips and the count are counted from published listings**, never from `taxonomy.py` — the
same rule as the browse links, and `homepage:hero` is in `publication.CACHE_KEY_PATTERNS` so a
suspension that empties a profession removes its chip within seconds. The scale line says
"listed", never "checked": publication does not require verification, and a test asserts the word
stays out. Tab stops before the grid went from 4 to 8 (4 controls, then 4 chips).

### The results register and the panelled sidebar (Phase 5c)

`/search/` rebuilt against a supplied reference: result rows with the commercial facts in a
rail, and a sidebar of three white panels with a count against every option. The CSS is the
`Phase 5c` block at the end of `static/src/app.css`; `docs/design-system.md` §10 lists every
override of a packaged rule with the measurement behind it.

Seven things are decisions rather than implementation.

* **The card has a `variant`, and it changes CSS — with one exception.** `register` puts the
  rail in a third column behind a hairline; `grid` (the home page) stacks it under the body.
  Same elements, same order, same ids, one modifier class — the Phase 5b search-bar rule, for
  the same reason: one keyboard pattern to keep right, not two. The exception is the **town**,
  which renders on the register and not in the grid. Phase 5's rule is that the home page needs
  two published practitioners in a town before it names it, because naming it beside one listing
  identifies that person by where they work. A results page is a different act — the visitor
  asked about a place, and the address is one the practitioner marked public. The rule is kept
  where it was written rather than widened or quietly dropped;
  `test_a_town_needs_more_than_one_practitioner_to_be_offered` is the line.
* **Counts are measured with each group's OWN selection cleared** (`search.facet_counts`).
  Count a group against a queryset that already has that group's filter applied and every sibling
  reads 0 the moment you tick one. Across groups the filters stay on, so the number answers "and
  how many of *these*". A **0** is rendered and the row disabled rather than removed — the
  vocabulary must not change shape between searches — and **a ticked row is never disabled**,
  because a disabled checkbox is not submitted and would silently drop the visitor's own filter.
  The under-18 gate is re-applied by hand in `facet_counts`, because clearing the speciality
  selection also clears what `_requests_minor_work` reads. One residual over-count is documented
  there and closes with the open `recompute()` fix.
* **"Show N more" APPENDS.** `hx-select` lifts the new `<li>`s out of the response,
  `hx-swap="beforeend"` puts them on the end of the list being read, and `hx-select-oob` swaps the
  control for the server's version of it. With script off it is a plain link to `?page=2` and
  "Previous page" is rendered on the full page only. **Both branches carry `id="results-end"`** —
  htmx restores focus by id, and this control replaces itself on every press, so without a stable
  id the last press drops focus to `<body>`. `hx-replace-url="false"` opts out of the form's
  inherited value, or the address bar would say `?page=3` while showing pages 1–3.
* **The verified badge is "Verified · what this means" with a hover/focus panel.** The date and
  the scope of the check moved into the panel; they did not go away. The trigger is a real link,
  the panel holds nothing interactive, and with script off `x-cloak` is never removed so the panel
  does not exist. Measured against WCAG 1.4.13: opens on `focusin`, dismissible with Escape
  without moving focus, hoverable. The panel names only `verification.BASE_REQUIRED` —
  **DBS is deliberately absent**, it is the safeguarding check and not one of the badge's
  required ones. **TODO(sign-off): Dr. Abbass** — all of that wording is gated copy.
* **"Paid placement", not "Featured".** The reference labels the slot "Featured", which says a
  listing was chosen and not that it was bought. The pill takes the reference's placement and
  colour and keeps the word that discloses it (`docs/content-compliance.md` §6).
* **No contact button on a card, ever.** The reference puts "Show contact details" on the row.
  Contact is revealed on the profile, through a POST, behind a per-IP limit. The rail's affordance
  is "View profile", deliberately the same size and weight as the reference's button.
* **The sidebar caps two lists and computes the "is anything inside selected" test in Python.**
  Six funding rows then "All 15 funding options"; six categories then "Browse all N categories".
  A closed `<details>` removes its contents from the accessibility tree, so a selection in the
  tail has to force the group open (funding) or be sorted to the front (categories) — and "does
  any speciality in this category appear in the selection" is not something a Django template can
  ask. A template that cannot ask it silently answers no.

**Three things only the browser showed**, which is the recurring lesson with three more
instances:

* **A `<select>`'s min-content width is its longest option.** "Typical wait" refused to go below
  231px and dragged the sidebar track to 273px inside a 264px container at 320.
* **The rail wore two borders at once.** The stacked layout's `border-block-start` was never
  reset in the `≥56rem` rule that adds the vertical one, so the right-hand column had a stray
  rule across its top separating nothing from nothing.
* **The live region announced a page nobody was on.** After one "Show 9 more" it read
  "29 practitioners · page 2 of 2" while all 29 rows were on screen. `_result_count.html` now
  says "showing N" under htmx and keeps "page N of M" for the paging, no-JavaScript reading.

The register also costs **five queries for the counts and two for the card** (languages, and the
public location the distance was measured to). None of them is per-row, and
`test_the_card_queries_do_not_grow_with_the_page` is what keeps it that way — the count test
alone passes at a page size of one.

### The practitioner dashboard (Phase 6)

`/dashboard/` → `dashboard.views` → `apps/dashboard/services/{overview,editing,insights}.py`. Every view
carries `can_use_dashboard` **and** `owns_practitioner` through the `practitioner_view` decorator,
and **no dashboard URL names a practitioner** — the listing comes from the session, so there is no
per-view authorisation decision to forget. `test_no_dashboard_url_names_a_practitioner` guards
that, because `/dashboard/<pk>/` "so staff can help somebody" is one line away.

Six things are decisions rather than implementation.

* **`completeness` had no writer.** It has been a ranking input since Phase 1 (15% of the search
  score) and the home-grid gate since Phase 5 — and nothing ever wrote it, so every real listing
  sat at the default 0: bottom of every tie-break and absent from the front page.
  `seed_demo` hard-coded plausible numbers and the factory defaulted to 80, which is exactly why
  it looked fine. `apps/directory/services/completeness.py` is the single writer, wired through
  `apps/directory/signals.py` with a `post_save` **and four `m2m_changed` receivers** — one relation is
  not enough, the same lesson as the search vector. **The score and the checklist are one list**:
  a meter saying 68% and advice saying something else would be two sources of truth about the
  same question.
* **"Controlled" and "safe" are `review.CONTROLLED_FIELDS`, imported, never restated.**
  `apps/dashboard/services/editing.py` derives from it so the label a practitioner reads and the
  behaviour they get cannot disagree. **The copy says the listing stays online**, because
  "re-enters review" reads as "my page disappears" and somebody who believes that will not fix
  their own typo — which is the opposite of what the control is for.
* **Changed-field detection is the whole correctness problem.** Row fields and `client_groups`
  come from `form.changed_data`; `registrations` and `qualifications` are separate models and
  appear in it not at all, so `editing.save()` takes them as an explicit `related_changed`
  argument. A caller that forgets is a badge left on against an unchecked GMC number, which is
  why it is a required argument rather than something inferred.
* **The lint runs on every edit, not just at submission.** `apps/directory/services/lint.py` was
  written for `review.submit()` — the one-time draft→published path. Phase 6 is the first time a
  practitioner can edit a **live** listing's free text, and a safe-field edit publishes
  immediately, so without `dashboard.forms.LintedPractitionerForm` a published practitioner could
  put a prescription-only medicine name into their intro and it would be public the moment they
  pressed Save. Only findings about fields *this* form shows become errors — otherwise a bad
  intro would block somebody from editing their opening hours, with an error pointing at a field
  that is not on the page.
* **Evidence upload fails closed.** `apps/directory/services/antivirus.py` defaults to `reject`:
  with no scanner configured, every upload is refused. A control that switches itself off when it
  cannot reach its dependency is not a control — the same reasoning as the POM dictionary, which
  raises rather than permitting everything. `skip` exists for development, is named so it cannot
  be mistaken for anything else, and `prod.py` deliberately does not set `ANTIVIRUS_BACKEND` at
  all. **An upload cannot advance the practitioner's own verification state**: it moves a check
  to SUBMITTED and never off VERIFIED, and touches no computed field.
* **Withdrawal is one atomic act.** `publication.withdraw_consent()` closes **every** open
  `ConsentRecord` and unpublishes in one transaction, because an unpublish that forgets the
  consent row leaves the record saying somebody still consents to a listing that is gone.
  `request_removal()` is the stronger, separate ask — it additionally sets `Document.delete_after`
  from `EVIDENCE_RETENTION_DAYS`, which carries a `TODO(sign-off)` rather than a confident number
  (`docs/verification-policy.md`: "a solicitor question... leave it configurable"). There is no
  "contact us to be removed" anywhere in the flow, and a test asserts it stays that way.

**Two-factor is now available to practitioners.** Phase 0 built the flow and gated every entry
point on `requires_two_factor`, which is staff-only — so the brief's "available to practitioners,
mandatory for staff" was half built. `access.py` gained `can_enrol_two_factor` (anyone signed in),
`must_challenge_two_factor` (staff by role, **or anyone who has enrolled** — a second factor
somebody opted into and is never asked for is worse than none, because they believe they have it)
and `can_disable_two_factor` (practitioners yes, staff no). The middleware asks the second
question, not the first. A consequence worth knowing: a session that has not passed the challenge
cannot turn the challenge off, which is what stops an unattended half-authenticated session
disabling it.

### What the Phase 6 gate found

**Eleven blockers across three reviewers, and none was visible to a green suite of
1037 tests.** Two of them reopened doors that earlier phases had closed.

**Compliance — three:**

* **A practitioner could put a restricted title on their own live listing.**
  `blocking_publication_reasons()` ran only at `approve()` and `lift_suspension()`,
  both staff transitions, so the dashboard reached the forbidden state two ways:
  change `profession` to a title with no verified registration, or delete the
  verified `Registration` the title already rests on. Both kept the listing
  PUBLISHED — right for a surname, wrong for a protected title — and left it in the
  HTML and in `jobTitle`. `editing.save()` now calls the authoritative function
  **after** the write and inside the transaction, so a third route to the same
  place cannot slip past a predicate that only knew about two.
* **POM names reached a live profile through free text the lint never read.**
  `LINTED_FIELDS` reads attributes off the `Practitioner` row, so
  `PractitionerLocation.label` / `days_at_site` and `Qualification.title` were never
  scanned — the Phase 3 carry-forward, made exploitable by self-service editing.
  `lint.related_texts()` closes it for `review.submit()`, and `LocationForm` lints
  its own fields because a location edit publishes before `lint.run()` could see it.
* **HOLD findings held nothing.** §2 and §4 both say flagged copy "holds in review
  rather than auto-publishing". `intro` is a SAFE field, so an efficacy claim went
  live immediately — under a message promising a reviewer would look at it.
  `submit_update()` now takes `held_fields`.

**SEO — two:**

* **A taxonomy edit could republish ungated child work for 24 hours.** Phase 5
  filtered `implies_minors` specialities when the twelve were *selected*; the grid
  caches ids. Before Phase 6 only staff could change a live listing's specialities,
  so selection-time filtering was sufficient. Now `_hydrate()` re-applies the
  exclusion — the same fail-safe reasoning as the `PUBLISHED` re-check — and every
  dashboard save busts the cache.
* **The email-confirmation GET failed OPEN under mail-gateway prefetching.**
  Defender SafeLinks and friends fetch every URL in a message; the GET recorded the
  confirmation, so with both mailboxes behind a scanner the sign-in credential moved
  with no human action. It is a POST behind a button now. Note the asymmetry that
  made this obvious in hindsight: `magic_link_consume_view` is also a
  token-consuming GET, and a prefetch there fails *safe*.

**Accessibility — six, four of them one root cause.** Setting `aria-describedby` in
a form's `__init__` meant Django would never add the error id
(`BoundField.aria_describedby` gives up when the attribute exists), and
`id_for_label` is `""` for grouped widgets — so every `client_groups` checkbox
carried the literal `aria-describedby="-controlled"` and the delivery-mode radios
had `<label for="">`. The wiring is built at render time now, from `auto_id`, with
`use_fieldset` deciding label vs legend. Plus reflow at 320px on five pages (file
inputs, long `.btn` labels, an unwrapped email address, two wide tables) and a
scroll container whose comment claimed keyboard reachability the markup did not
implement. Gaps 22–23.

**Not fixed, and flagged:** nothing writes `ConsentRecord` anywhere in this project
— the lawful basis for publishing is recorded nowhere, which is a Phase 2 gap too
large for this phase. `blocking_publication_reasons()` now refuses a listing whose
consent was *withdrawn*, which is the half Phase 6 opened.

### Post-Phase-6 corrections (naming, layout, admin, staff routing)

Not a phase — five requested changes and what each turned up. Two were latent bugs
nothing in 1074 tests could see, both for the same reason: **no test exercised the
surface.**

* **The site is the "Kiam Clinic Directory".** `KIAM_UI["SITE_NAME"]` said "Kiam
  Directory" from Phase 0, so `<title>` and `og:site_name` disagreed with the
  `WebSite` entity `seo/jsonld.py` emitted on the same page (`DIRECTORY_NAME` has
  always been right). `BRAND_PREFIX` / `BRAND_ACCENT` are now set **explicitly**:
  kiam-ui derives the wordmark by splitting `SITE_NAME` on the first space, which
  would have accented "Clinic Directory" and put the publisher's own name in the
  brand colour. It is "Kiam Clinic" + green "Directory", because the half that has
  to be visible is the one that says this is not the clinic.
* **The contact address is `info@kiamclinic.com`**, not the main site's
  `enquiries@`. An enquiry about a listed practitioner is not a clinic enquiry.
  `DEFAULT_FROM_EMAIL` (`no-reply@`) is a sending address and is untouched.
* **No account could be created through the Django admin, at all.**
  `AdminUserCreationForm` *declares* `usable_password`, `password1` and
  `password2`; declared fields are required whether or not a fieldset lists them,
  and `add_fieldsets` listed none of them — so the page rendered without the
  inputs and every POST failed on "This field is required" for two controls that
  were not on it. The general guard is
  `test_every_required_field_on_the_add_form_is_rendered`, which asserts the
  *shape* (nothing the form insists on is missing from the page) rather than
  today's field names.

  `password` is back on the change fieldsets too. It was omitted on the reasoning
  that a settable password is "a second, unaudited way in" — true while magic link
  was the only route, and not since Phase 1. Omitting it never prevented password
  authentication; it only prevented an admin helping somebody locked out of it.
  The field is `ReadOnlyPasswordHashField` and Django's change-password view
  writes a `LogEntry` naming the admin. The add form now also refuses a
  **case-different duplicate address**: `email` is unique but case-SENSITIVE and
  `normalize_email()` lowercases only the domain, so two such rows break *both*
  accounts — `CaseInsensitiveEmailBackend` fails closed on
  `MultipleObjectsReturned` and `magic_link` would mail whichever came back first.
* **`/dashboard/` 404ed for staff, and the header sent them there.** kiam-ui
  renders one "Dashboard" link from `KIAM_UI["AUTH"]["DASHBOARD_URL"]` for every
  signed-in account; `AUTH` is not in kiam-ui's `RESOLVABLE` set, so it cannot vary
  by role without forking the package. `practitioner_view` redirects staff to
  `backoffice:dashboard` — guarded by `can_review_submissions`, the same predicate
  that decides the redirect, so the destination cannot 403. **`can_use_dashboard`
  is unchanged**: staff are redirected, not admitted, and the 404 is kept for the
  case it was written for, an account with no listing. Design-system gap 24.
* **The apps moved to `apps/`** — see "Layout" above. The move was mechanical
  except for one thing, and that one thing is the lesson: a **hand-written
  auth-backend path** in `magic_link.log_in()` and `passwords.log_in()` silently
  signed everybody out, because `auth.get_user()` returns `AnonymousUser` for a
  backend not in settings without raising or logging. Now
  `apps.accounts.backends.BACKEND_PATH`, derived from the class, with a grep test
  over `accounts/services/` so the next module cannot write one.

### Still to build

Phase 7: insights rollups, landing pages and launch prep. See `docs/roadmap.md`.

`apps/directory/services/search.py` was recovered from the rooms repo at Phase 4
(`git -C ../rooms show f49d0c2^:search.py`) and is now in this repo, reviewed and fixed.
`homepage_grid()` is in use from Phase 5 and has tests; its daily seed moved from the UTC date to
`timezone.localdate()` so it rotates when the result shuffle does.

---

## How to work here

- **Work in phases.** Follow `docs/roadmap.md`. Do not jump ahead or chain phases.
- **Pause at each gate.** Print what changed and what needs human confirmation, then stop.
- **Keep these current after every phase** — they are working docs, not intent docs:
  `docs/architecture.md` (app map, models, URLs, roles), `docs/design-system.md` (whenever the
  kiam-ui pin moves), this file's "What already exists" section, and `README.md`.
- The intent docs (`multi-project-architecture`, `seo`, `content-compliance`,
  `verification-policy`, `roadmap`) are human-authored. **Propose changes, don't silently
  rewrite them.**
- **Some source files are authored elsewhere and adopted verbatim** — `apps/accounts/models.py`,
  `apps/accounts/access.py`, `apps/directory/models.py`, `apps/directory/taxonomy.py`,
  `apps/directory/services/verification.py`. Review, don't rewrite. Where one genuinely had to change
  (two blockers in `apps/directory/models.py`, one bug in `verification.py`) the reason is commented in
  place, and `pyproject.toml` carries per-file lint ignores rather than editing them for style.
  Additions go in a clearly marked block at the end of the file, not interleaved.
- Migrations: one logical change per migration where practical, and always reviewable.
- **Run the real thing before calling a phase done.** Three of the worst bugs in this repo passed
  the suite and were only found by starting the server or building the image: a `.env` baked into
  a Docker layer, `collectstatic` failing on the kiam-ui CSS source, and password sign-in
  rejecting the correct password for a mixed-case address.

## Review subagents (run before closing a phase)

Their definitions live in **`.claude/agents/`** (copied from `docs/` in Phase 2, where they had
agent frontmatter but were not somewhere Claude Code looks). A newly added agent only registers at
session start, so if one is missing, restart rather than reimplementing it inline.

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
