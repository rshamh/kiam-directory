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
> `directory/tests/test_profile_minor_gate.py` and `directory/tests/test_search_minors_gate.py` —
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
> **STILL OPEN, and now more exposed than it was.** Only *client groups* are gated. A speciality
> with `implies_minors=True` — "Child & adolescent ADHD assessment" — is neither gated on the
> profile nor gated in search, and a listing that tags one while selecting only adult client groups
> never requires a DBS at all (`recompute()` derives the requirement from client groups alone).
> Phase 4 makes this worse in two ways: a **free-text query** for "child adhd" matches that
> speciality through the search vector, and a **speciality facet** matches it directly — neither of
> which goes anywhere near the client-group gate. The one-line fix is in `recompute()` so all three
> surfaces move together; it is a safeguarding decision and waits for **Dr. Abbass / CQC lead**.
> `/how-verification-works/` is narrowed to claim only what is enforced in the meantime.

---

## Stack

- Django (latest production-ready), PostgreSQL + **PostGIS** via GeoDjango, Redis cache.
- Tailwind + **HTMX** + Alpine.js.
- **`kiam-ui`** — the shared UI package, pinned:
  `kiam-ui @ git+ssh://git@github.com/rshamh/kiam-ui@v1.0.1`
- Auth: email identifier, no usernames, thin custom `User`. All role checks in
  `accounts/access.py` and nowhere else. **Two sign-in routes, both live:**
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

Pinned to `v1.0.1`. **Upgrade deliberately** — bump the pin, read the changelog, run the visual
check, commit as its own change. Never track a branch.

**`docs/design-system.md` already documents its real API** — base template, all 24 block names,
every `KIAM_UI` settings key, all 17 component partials with parameters, the token families and
the documented WCAG contrast pairings. Read that rather than guessing, and **re-read the installed
package and update it whenever the pin moves**. Do not re-specify colours, type or spacing
anywhere in this repo — `kiam-ui` is the source of truth for those.

Its §7 "Gaps" is the list of things the package does not give us and how each is worked around.
Two matter for the next phases:

- **Fonts load from Google Fonts on every page**, before any consent decision (Gap 3). Unresolved,
  and it collides with golden rule #4 the moment analytics or consent lands.
- **`collectstatic` needs the override in `seo/management/commands/`** (Gap 9), because the package
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
SearchBar, LocationInput and RadiusSelect in one partial), `_filter_sidebar.html`,
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

    accounts/services/    magic_link · passwords · two_factor · ratelimit
    directory/services/   verification · lint · documents · search_index · profile · metrics
    backoffice/services/  invites · review · publication · concerns

**Only `directory/services/verification.py` may write the five computed fields.** Nothing else,
ever. `directory/tests/test_admin_readonly.py` enforces this by walking the whole admin registry —
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

`directory/services/lint.py`, run by `review.submit()`. BLOCK on `pom` and `contact`; HOLD on
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

`directory/factories.py`. Traits: `published`, `verified`, `provisional_dbs`, `dbs_cleared`,
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

`/p/<slug>/` → `directory.views.profile` → `directory/services/profile.py`. Six things about it
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
* **`BreadcrumbList` JSON-LD comes from kiam-ui's breadcrumb partial**, not from `seo/jsonld.py`,
  because the partial emits it from the same `items` list it renders the visible trail from.
  `jsonld.breadcrumb_items()` builds that list with absolute URLs. Do not add a second
  `BreadcrumbList` to the graph.

**Slug redirects are two signals, and both are needed.** `capture_previous_slug` (pre_save) is
the last moment the old slug is readable; `write_slug_redirect` (post_save) is the first moment
the new one is known to have committed. Writing the row from pre_save leaves one behind for a
save that then failed. The redirect is written only when `published_at` is set — a listing that
was never live has no inbound links worth preserving — and a slug returning to use (A→B→A) has
its stale row deleted so it cannot occupy the unique `old_slug`.

**Static pages are a registry, not eight views.** `pages/content.py` holds URL, name, title, meta
description, template and sitemap priority per page; `pages/urls.py` and `seo/sitemaps.py` both
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

`/search/` → `search.views.search` → `search/services/params.py` (parsing) →
`directory/services/search.py` (the query) → `search/services/geocode.py` (postcode → point).

`directory/services/search.py` was **adopted from the rooms repo's history**, and its module
docstring lists the five things that had to change. Four were bugs it shipped with; the tests that
pin them are grouped under `ADOPTION FIX n` headings in
`directory/tests/test_search_service.py`, so a "simplification" that reintroduces one fails a test
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
form serialises only the filters. `search/tests/test_search_view.py::test_search_works_completely_without_javascript`
is the one that must never be allowed to fail.

The result count is a **persistent live region outside `#results`**, updated by
`hx-swap-oob="innerHTML:#result-count"`. A live region that is itself replaced by the swap is not
announced — the same lesson as the Phase 3 contact reveal.

### The home page (Phase 5)

`/` → `pages.views.home` → `pages/services/home.py`. Five things about it are decisions, not
implementation.

* **The hero search is the SAME component as `/search/`.** `components/_search_bar.html`, wrapped
  in a plain `<form method="get" action="/search/">`. The brief calls the hero the most important
  interaction on the site, and a second implementation of the query field, the location field's
  native `<datalist>`, the radius `<select>` and the always-visible submit button would be a
  second one to keep accessible. Only 4 tab stops before the grid, and the whole thing is a plain
  GET with script off — `test_the_hero_search_works_completely_without_javascript` is this phase's
  equivalent of the search page's non-negotiable test.
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
`directory/services/images.py`, 80/160/240px, built inside the cached grid path so twelve
conversions happen once a day. Names are deterministic from the original's, so a replaced headshot
gets new rendition names and there is nothing to bust. It is **all-or-nothing and never fatal** —
any failure returns `""` and the card falls back to the plain `src`, because a `srcset` naming a
rendition that does not exist is a broken image. Re-encoding drops EXIF, which takes the GPS
coordinates out of a phone photo. `/search/` is unchanged: it builds no renditions, so it emits no
`srcset`.

### What the Phase 5 gate found by running the real thing

Four defects, all fixed and covered. **None of them was visible to the test suite**, which is now
the fifth and sixth entries in that list in `README.md`:

* **`extra={"name": ...}` in a log call raises.** `name` is a reserved `LogRecord` attribute and
  `Logger.makeRecord` raises `KeyError` on a collision — so the log line meant to *record* a
  failure *became* the failure, and turned "a broken image never breaks the page" into a 500 on
  the home page. The suite could not see it: `config/settings/test.py` sets the root logger to
  CRITICAL, so `isEnabledFor` is False and `makeRecord` is never reached.
  `test_the_logging_calls_are_actually_emittable` turns logging on for exactly this.
* **Concurrent requests wrote duplicate renditions.** Django's storage never overwrites, so the
  loser of a write race got a suffixed name nothing would ever request. Measured: five concurrent
  cold-cache loads produced 111 orphans. `_write` now deletes the file it just lost with. There is
  deliberately still no lock — see the module docstring for why.
* **`.dir-steps` had no numbers.** Tailwind's preflight sets `ol { list-style: none }`, and this
  class is used where the sequence *is* the information — WCAG 1.3.1, on the home page and on
  `/two-factor/setup/` since Phase 0. Fixed on the component, not on a containing `.prose`.
* **The footer wordmark accent measured 1.76:1.** `--brand-primary` on `--surface-inverse`, in the
  chrome, on every page since Phase 0. Accessibility went 94 → 100. Recorded as design-system
  gap 17, with gap 18 for the related "kiam-ui sizes no heading but
  `.ds-section-head__title`" — which had left `.prose h2` at body size on eight static pages since
  Phase 3.

**Measured Core Web Vitals** (Lighthouse 13.4.1, production-like local server — `DEBUG` off so
WhiteNoise serves the compressed static a visitor gets; 3 mobile runs, identical):

| | Mobile, Slow 4G (1,638 Kbps, 150 ms RTT, 4× CPU) | Desktop |
|---|---|---|
| Performance | 89 | 99 |
| LCP | 3.20 s | 0.77 s |
| CLS | 0.000 | 0.014 |
| TBT | 0 ms | 0 ms |
| FCP | 2.60 s | 0.69 s |
| Accessibility | 100 | 100 |
| Weight | 201 KiB / 13 requests | 211 KiB / 19 requests |

LCP is the only metric outside "good" and **it is not this page's markup**: 1,510 ms of it is
render-blocking stylesheets, of which Google Fonts is 796 ms and 68 KiB. Blocking that origin
takes mobile LCP to 1.84 s and the score to 98. That is design-system **gap 3** with a number on
it, and it collides with golden rule #4 — the fix is self-hosting the two families upstream in
`kiam-ui`, not here. The next item after it is the **HTML document, 81 KiB uncompressed**: nothing
in the stack gzips a response body (`GZipMiddleware` is deliberately absent — BREACH), so that
belongs to the reverse proxy or CDN at Phase 7 launch prep.

### Still to build

Phases 6–7: the practitioner dashboard, and insights / landing pages / launch prep. See
`docs/roadmap.md`.

`directory/services/search.py` was recovered from the rooms repo at Phase 4
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
- **Some source files are authored elsewhere and adopted verbatim** — `accounts/models.py`,
  `accounts/access.py`, `directory/models.py`, `directory/taxonomy.py`,
  `directory/services/verification.py`. Review, don't rewrite. Where one genuinely had to change
  (two blockers in `directory/models.py`, one bug in `verification.py`) the reason is commented in
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
