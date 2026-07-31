# Kiam Clinic Directory

`directory.kiamclinic.com` — a public directory of independent mental-health practitioners.
Kiam Clinic is the publisher and introducer only. Clients contact and pay practitioners
directly.

Django · PostgreSQL/PostGIS · Redis · Tailwind + HTMX + Alpine · [`kiam-ui`](#kiam-ui)

**Phases 0–6 are built.** Foundation, data model and taxonomy, the admin back office, the public
profile with its static pages, search, the home page, and the practitioner dashboard. Phase 7
(insights rollups, landing pages, launch prep) is next.

## Start here

1. `CLAUDE.md` — working rules, and a "What already exists" section listing the names, services
   and invariants that will otherwise bite you
2. `docs/multi-project-architecture.md` — how this sits alongside the main site and rooms
3. `docs/architecture.md` — apps, models, URLs, roles, as they actually are
4. `docs/design-system.md` — what `kiam-ui` really exposes, and the twenty-four gaps
5. `docs/roadmap.md` — the eight build phases and their gates

**Layout.** Every Django app lives under `apps/` — `apps/accounts/`, `apps/directory/`,
`apps/search/`, `apps/dashboard/`, `apps/backoffice/`, `apps/seo/`, `apps/pages/`. `config/`
(settings, root URLconf, WSGI/ASGI), `templates/`, `static/`, `ops/` and `docs/` stay at the root.
App **labels** are unchanged — Django takes the label from the last path component, so
`apps.accounts` is still `accounts`, which is what `AUTH_USER_MODEL` and every migration resolve
against. Docstrings name modules app-relative (`directory.services.verification`); imports are
fully qualified (`apps.directory.services.verification`).

---

## Local setup

### 1. System dependencies

GeoDjango loads **GDAL** and **GEOS** through `ctypes` at import time, and the database must be
**PostGIS**, not plain Postgres. Redis backs the cache and the magic-link rate limiter.

```bash
# macOS
brew install gdal geos postgresql@17 postgis redis
brew services start postgresql@17
brew services start redis
```

```bash
# Debian / Ubuntu
sudo apt install postgresql-17-postgis-3 gdal-bin libgdal-dev libgeos-dev redis-server
```

> **macOS only — GDAL_LIBRARY_PATH.** Homebrew on Apple Silicon installs to `/opt/homebrew/lib`,
> which is not on the linker's default search path, so `ctypes.util.find_library` cannot find
> either library and Django fails at startup with `Could not find the GDAL library`. Set both
> paths in `.env`:
>
> ```
> GDAL_LIBRARY_PATH=/opt/homebrew/lib/libgdal.dylib
> GEOS_LIBRARY_PATH=/opt/homebrew/lib/libgeos_c.dylib
> ```
>
> Leave them blank on Debian/Ubuntu and in Docker, where the packages land on the default path.

> **PostGIS packaging.** Homebrew's `postgis` formula is built for specific PostgreSQL majors —
> at the time of writing, 17 and 18, **not 16**. Installing `postgresql@16` gives you a server
> whose `CREATE EXTENSION postgis` fails with "extension is not available". Check with
> `ls $(brew --prefix postgis)/share/` before choosing a major.

### 2. The `kiam-ui` SSH key

`kiam-ui` is a **private** repository installed over SSH:

```
kiam-ui @ git+ssh://git@github.com/rshamh/kiam-ui@v1.0.1
```

pip carries no credentials of its own, so `git+https://` will not work — the pin is deliberately
`git+ssh://`.

**On a development machine.** Nothing extra if you already have GitHub SSH access. Check it:

```bash
ssh -T git@github.com
```

**On a build box, a CI runner, or any machine that is not yours.** Create a **read-only deploy
key** for the `rshamh/kiam-ui` repository (Settings → Deploy keys → *Add deploy key*, leave
"Allow write access" unticked), and put its private half where the build can reach it:

- **A server or VM** — write the key to `~/.ssh/kiam_ui_deploy`, `chmod 600` it, and add:

  ```
  Host github.com
    IdentityFile ~/.ssh/kiam_ui_deploy
    IdentitiesOnly yes
  ```

  to `~/.ssh/config`. Then `ssh-keyscan github.com >> ~/.ssh/known_hosts` so the first fetch is
  not an interactive prompt.

- **GitHub Actions** — store it as the repository secret `KIAM_UI_DEPLOY_KEY` and load it into
  an agent before `pip install`. `.github/workflows/ci.yml` already does this.

- **A Docker build** — do **not** copy the key into the image. Forward the agent instead:

  ```bash
  docker build --ssh default .
  ```

  The `Dockerfile` mounts it with `--mount=type=ssh`, so the key is available to pip and never
  enters a layer.

### 3. The project

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env          # then fill it in — at minimum SECRET_KEY and, on macOS,
                              # GDAL_LIBRARY_PATH / GEOS_LIBRARY_PATH

createdb kiam_directory
psql kiam_directory -c "CREATE EXTENSION postgis; CREATE EXTENSION pg_trgm;"

python manage.py migrate
python manage.py seed_taxonomy      # the controlled vocabulary; safe to re-run
python manage.py seed_demo --fresh  # 28 searchable practitioners — DEVELOPMENT ONLY
python manage.py runserver
```

`seed_demo` gives you something to search: 28 published practitioners spread across
the UK, every DBS state, current and lapsed badges, online-only and in-person-only,
four featured listings so the paid-placement cap has something to cap, and one
practitioner whose in-range office has steps while their step-free office is 200
miles away. Verification goes through the real service, so a demo badge is backed by
the same dated evidence a real one would be.

**It refuses to run with `DEBUG` off, and there is no `--force`.** It creates
fictional practitioners carrying "Credentials checked" badges, and that is a claim
Kiam makes about a real person's documents.

`.claude/launch.json` runs the dev server on **8010**, not 8000: the main site and the
room-rental app are the other two checkouts on this machine and one of them usually has
8000. Nothing in the app depends on the port — `SITE_BASE_URL` only feeds canonicals in
mail and the sitemap, and page canonicals come from the request.

### Scheduled jobs

Two nightly commands, defined in `ops/crontab`:

| Time | Command | Why |
|---|---|---|
| 03:00 | `verification_sweep` | Lapses badges whose evidence expired, closes elapsed provisional-DBS windows, sends the 60/30/7/0-day reminders |
| 03:30 | `rebuild_search_index` | Safety net behind the search-vector signals — and the only thing that picks up a synonym added to `taxonomy.py` |

A missed `verification_sweep` night is a reminder nobody receives: the thresholds are exact
day buckets. Re-run it the same day rather than waiting.

### 4. The stylesheet

```bash
npm install
npm run build:css        # runs `manage.py kiam_ui_vendor`, then Tailwind
```

`build:css` vendors `kiam-ui`'s CSS source and templates into `build/kiam-ui/` first. That step
is not optional and not cosmetic: Tailwind cannot see into `site-packages`, so any utility used
only inside a packaged template is purged from the production build. `kiam_ui/base.html` puts
`bg-page text-body font-sans antialiased` on `<body>`. The failure is invisible in development,
where nothing is purged.

**Re-run `npm run build:css` after every `kiam-ui` pin bump.** `build/` and `static/css/` are
build output and are gitignored.

---

## Docker

```bash
docker compose build --ssh default
docker compose up
```

Three services — `web`, `postgis`, `redis` — on their own network and their own volume. Nothing
is shared with the main site or with rooms; see `docs/multi-project-architecture.md` §6. Host
ports are 8000, **5433** and **6380**, so a local Postgres and Redis keep their usual ports.

```bash
WEB_PORT=8001 docker compose up      # if something already has 8000
```

Two things about this stack that are not obvious:

- **`postgis/postgis` publishes amd64 only**, so the service pins
  `platform: linux/amd64` and runs under emulation on Apple Silicon. Slower than native,
  immaterial for a development database, and a no-op on x86.
- **The `web` service sets `READ_DOT_ENV_FILE=False`.** The repo is bind-mounted for live
  reload, which brings your `.env` into the container; without this, every key Compose does not
  set would be filled in from your machine's file. In a container the environment is the
  configuration.

---

## Before every commit

```bash
ruff check . && ruff format --check . && pytest
```

CI runs the same three, plus `makemigrations --check` and a full image build.

**Run the app, not just the suite.** Five of the worst bugs found so far passed a green suite:
a developer `.env` baked into a Docker layer, `collectstatic` failing on kiam-ui's shipped CSS
source, password sign-in rejecting the *correct* password for a mixed-case address, a closed
`<details>` that did not collapse (317 tab stops instead of 35), and — at Phase 5 — a reserved
`LogRecord` key that turned "a broken image never breaks the page" into a 500, invisible to the
suite because test settings silence logging. A green test run is not evidence the thing starts.

```bash
python manage.py runserver          # then actually load a page
docker compose build --ssh default  # the image is where the .env leak surfaced
```

**Measure the rendered page before closing a phase with a performance or accessibility gate.**
Reading the markup did not find the collapsed-`<details>` bug and would not have found the
footer wordmark at 1.76:1. Lighthouse against a production-like local server — `DEBUG` off, so
WhiteNoise serves the compressed static files a visitor actually gets — is what the Phase 5 gate
used; see `docs/roadmap.md` for the numbers it produced.

## Testing

~1,040 tests. Two conventions worth knowing before adding more:

- **Factories never write a verification field.** `PractitionerFactory(verified=True)` creates
  dated checks and calls `recompute()`, exactly as production does. A factory that set
  `is_verified` directly would make every downstream assertion a test of the factory.
- **Side-effecting factory traits are `post_generation`, not `factory.Trait`.** A Trait sets
  values in the attributes phase; pointed at a post-generation hook it either raises or silently
  skips. Call syntax is the same either way.

```bash
pytest apps/directory/tests/test_search_minors_gate.py   # the Phase 4 gate: PROVISIONAL is not in minors results
pytest apps/directory/tests/test_profile_minor_gate.py   # the Phase 3 gate: minor groups are not in the HTML
pytest apps/directory/tests/test_verification.py         # the service that decides what the public sees
pytest apps/backoffice/tests/test_flow.py                # invite -> draft -> submit -> verify -> publish
pytest apps/pages/tests/test_home.py                     # the Phase 5 gate: the hero works with no JavaScript
pytest apps/pages/tests/test_home_service.py             # the grid rotates daily, caches, and cannot show a suspension
pytest apps/pages/tests/test_home_minors_gate.py         # the Phase 5 gate: no un-cleared child work on the front page
pytest apps/dashboard/tests/test_gate.py                 # the Phase 6 gate: controlled vs safe edits, and one-click unpublish
pytest --create-db                                  # after a migration, or the reused DB will lie
```

---

## What's built

### Public pages

| Page | What it does |
|---|---|
| `/` | Hero search — query, location and radius side by side, submitting as a plain GET to `/search/`. Then a grid of twelve published listings that changes every day, the independence notice above it, how it works, what "Credentials checked" means, browse entry points counted from live listings, and cross-links to kiamclinic.com. The grid and the browse lists are cached for 24 hours; **the cache holds primary keys, not rows**, so a suspended listing drops out on the next request even if nothing busted the key |
| `/search/` | Filter sidebar and results. **Works completely without JavaScript** — one plain `<form>`, a visible submit button, full results in the body. HTMX swaps `#results` when it is available. Every faceted URL is `noindex, follow` with a canonical to the bare path |
| `/p/<slug>/` | A practitioner's profile. **PUBLISHED only** — a draft, a suspension and a slug nobody has used all return the same 404, so a suspension is invisible rather than announced. Old slugs 301 to the current one |
| `/p/<slug>/contact/<channel>/` | Reveals one contact detail. POST only, rate limited, `noindex`. Works without JavaScript as a whole page, and with HTMX as an in-place swap |
| `/about/` · `/how-verification-works/` · `/for-practitioners/` · `/accessibility/` | Written content. The verification page states plainly what the badge does **not** mean |
| `/terms/` · `/privacy/` · `/cookies/` | Outlines only. `TODO(sign-off)` — solicitor |
| `/report-a-concern/` | Writes a `ConcernReport`. Routes complaints about **care** to the practitioner's regulator, and names them, above the form |

The home page's hero uses the *same* `components/_search_bar.html` as `/search/` — one
implementation of the query field, the location field's native `<datalist>` autocomplete, the
radius `<select>` and the always-visible submit button, because a second copy on the most
important page on the site is a second copy to keep accessible.

Two rules run through all of it. Client groups render through
`Practitioner.visible_client_groups()`, so a `PROVISIONAL` practitioner's under-18 groups are
absent from the HTML rather than hidden in it. And contact details are never in the profile's
context at all — the reveal endpoint fetches them — so the page cannot leak one by accident, and
neither can its JSON-LD.

### Practitioner dashboard — `/dashboard/` (the practitioner's own listing)

Overview, six editors, evidence upload, insights, account & security, and one-click unpublish.
Every view is gated by `can_use_dashboard` **and** `owns_practitioner`, and **no URL names a
practitioner** — the listing comes from the session, so there is no per-view authorisation
decision for a future view to forget.

The thing worth understanding before changing anything here: **a controlled-field edit keeps the
listing online and takes the badge off.** Name, title, post-nominals, profession, registrations,
qualifications and client groups are checked against a document, so changing one reopens that
check and `recompute()` drops `is_verified`. Everything else — intro, services, photo, fees,
availability — publishes the moment they press Save. The UI says which is which *before* they
save, at the field, because a practitioner who thinks a correction will take their page down will
not make the correction.

### Back office — `/backoffice/` (staff only)

Everything to take a practitioner from invite to published. Every view carries an
`accounts.access` predicate, the whole prefix is behind the TOTP middleware, and it is
`noindex` plus `Disallow`-ed.

| Page | What it does |
|---|---|
| `/backoffice/invites/` | Issue an invite. Account creation is invite-only — there is no signup route |
| `/backoffice/review/` | Submissions awaiting a decision, with lint flags surfaced first |
| `/backoffice/review/<pk>/` | The frozen **snapshot** of what was submitted; approve / request changes / reject |
| `/backoffice/practitioners/<pk>/verification/` | One row per check type; verifier-only |
| `.../provisional-dbs/` | Grant an adult-work-only window. Second extension blocked pending Dr. Abbass |
| `/backoffice/concerns/` | Triage listing concerns. Registration doubts sort first |
| `/backoffice/audit/` | Read-only, filterable. No delete path anywhere |

### Verification is computed, never set — but granting it is one click

`directory.services.verification.recompute()` derives the badge and the under-18 gate from dated
`VerificationCheck` rows. Nothing else writes those fields — a test walks the whole Django admin
registry to enforce it. Insurance expiring lapses the badge overnight with no human action.

Admins still decide who is verified; they just do it by recording a decision, not by flipping a
switch. **"Verify all required checks"** in the workbench does the whole set in one action — one
insurance expiry date, one confirmation, one button. The date is required and not prefilled,
because it is the thing that makes the badge lapse on its own later.

DBS is not in that action. It is the safeguarding check, not one of the badge's required checks,
and it stays a separate deliberate act with its own expiry.

### Editing a live listing withdraws the badge, not the listing

Change a **safe** field — bio, fees, availability, photo — and it publishes immediately. Change a
**controlled** one — name, profession, registrations, qualifications, client groups — and the page
stays up while the badge comes off, because the checks that were made against the old details are
reopened and `recompute()` notices. Approving the new wording does *not* give the badge back;
re-verifying the evidence does. That distinction is the point: accepting a name change is not the
same as confirming somebody's photo ID matches it.

### Evidence access

Private files have no public URL; `.url` raises. `documents.open_evidence()` is the only door and
writes a `DocumentAccessLog` row in the same transaction as it mints a short-lived link. `admin`
deliberately cannot open evidence — that is the entire reason the `verifier` role exists.

### Submission lint

Runs at submit, before a human sees anything. Blocks on prescription-only medicine names and on
having no contact method; holds for review on efficacy claims, under-18 language without
clearance, and protected titles. Blocklist in `ops/pom-dictionary.txt`, editable without a deploy.

---

## Ops

| Endpoint | Purpose |
|---|---|
| `/healthz` | App, database and cache. 200 when all three are up, 503 otherwise |
| `/robots.txt` | This subdomain's own — never shared |
| `/sitemap.xml` | This subdomain's own |
| `/llms.txt` | This subdomain's own |

The Django admin is **not** at `/admin/`. It is at `ADMIN_URL_PATH` (default `staff-console/`),
and it requires the `superadmin` role with a verified TOTP device.

**Sentry** is wired in `config/settings/prod.py` and is a no-op when `SENTRY_DSN` is unset.
`send_default_pii` is off: a magic-link token or an email address must never reach the error
tracker.

**Logs** are one JSON object per line (`config/logging.py`). Development uses a plain formatter.
Auth and evidence events log the user id and never the credential — no magic-link token, no
evidence URL, no password.

**Review agents** live in `.claude/agents/` — `compliance-reviewer`, `seo-reviewer`,
`a11y-reviewer`. `CLAUDE.md` requires the relevant ones before closing a phase. They only
register at session start.

---

## Auth

Email is the identifier; there are no usernames. **Two sign-in routes share one form** at
`/accounts/login/`: type a password, or leave it blank and we email a link.

- **Magic link** — 32 random bytes, stored only as a SHA-256 hash, single-use, expiring after
  `MAGIC_LINK_TTL_MINUTES`. Always available, and it doubles as password recovery: sign in with a
  link, then set a new password. There is deliberately no separate reset-token flow — one
  recovery path, one expiry, one set of rate limits.
- **Password** — optional. Accounts are created *without* one and only get one if their owner
  sets it at `/accounts/password/`, where they can also remove it again. Argon2, 12-character
  minimum, validated against Django's stock validators.

Both routes are rate limited per email **and per IP** — the IP limit is what catches credential
stuffing, which a per-email limit cannot see. Neither route reveals whether an address has an
account: wrong password, unknown address, no password set and deactivated account all produce the
same message and the same status.

**There is no public signup route** — account creation is by admin invite. `admin`, `verifier`
and `superadmin` must additionally carry a TOTP device *regardless of which route they signed in
by*; `apps/accounts/middleware.py` keeps an unverified staff session on the challenge page and
`apps/accounts/access.py` refuses every staff capability until it is answered.

Every role check in the project lives in `apps/accounts/access.py`. Views and templates never inspect
`user.role`.

**Creating an account from the Django admin.** `/staff-console/accounts/user/add/` works, and its
"Password-based authentication" radio starts at **Disabled** — the normal state is no password and
a magic link. Set one only when the person has asked; they can set their own from the dashboard's
Account & security page. Case-different addresses are refused as duplicates, because `email` is
unique but case-sensitive and two such rows lock *both* accounts out of password sign-in.

**Staff have no practitioner dashboard.** `can_use_dashboard` is practitioner-only: acting on a
listing through that UI would have no audit actor and no review path, and the back office is where
staff do it. The header's "Dashboard" link is one URL for everybody (kiam-ui gap 24), so
`/dashboard/` redirects a staff account to `/backoffice/` rather than 404ing.

---

## Things that are easy to get wrong

**The under-18 gate has two halves and both are load-bearing.** `visible_client_groups()` covers
the profile; `filter(minor_work_status=CLEARED)` in `apps/directory/services/search.py` covers search.
Each has its own gate test, and the search one ends with a grep asserting both call sites still
exist — because deleting either leaves the other's tests green. Without the search half, a
`PROVISIONAL` practitioner — live for adult work with a DBS still pending — is returned to somebody
looking for a child therapist.

**Never add a second `.filter()` on `locations`.** Django resolves each one against its own join, so
two calls ask two independent questions: "any location in range" and "any location step-free" is
satisfied by a practitioner with an in-range office that has steps and a step-free office twenty
miles away. `search._location_filter()` builds one `Q` for exactly this reason.

**A suspended profile is a 404, not a page.** Phase 3 chose the 404 of the two options CLAUDE.md
allows, because a distinct "not currently listed" page is itself a signal: it says a listing was
here. A draft, a suspension and a slug nobody has ever used now render byte-identical pages.

**Contact details are not in the profile's context.** `profile.available_channels()` returns which
channels exist, never their values. If you find yourself adding an email address to the profile
view's context "just for the template", the reveal endpoint, the metric and the scraping
protection all stop meaning anything at once.

**Never write an auth-backend path as a string literal.** `login(request, user, backend=…)` puts
it in the session, and `auth.get_user()` returns `AnonymousUser` if it is not in
`AUTHENTICATION_BACKENDS` — no exception, no log line, no failing request. Both sign-in routes held
a literal `"accounts.backends.…"` until the apps moved under `apps/`, at which point the magic link
was consumed, the redirect was served, and the next page was anonymous. Import
`apps.accounts.backends.BACKEND_PATH`; it is derived from the class, so it cannot drift.

**A declared form field is required whether or not a fieldset renders it.** That is how the Django
admin spent six phases unable to create a single user: `AdminUserCreationForm` declares
`usable_password`/`password1`/`password2`, `add_fieldsets` listed none of them, and every POST
failed on required fields that were not on the page.
`accounts/tests/test_admin_user_creation.py::test_every_required_field_on_the_add_form_is_rendered`
asserts the shape rather than the field names.

**Session cookies stay host-only.** Do not set `SESSION_COOKIE_DOMAIN`. Sharing the cookie
across `.kiamclinic.com` requires a shared `SECRET_KEY`, a shared session store and a shared user
table — i.e. one database — which defeats the project separation entirely
(`docs/multi-project-architecture.md` §3 Option C, §4).

**The two storage backends are never merged.** Headshots go to the public backend; verification
evidence goes to `STORAGES["private"]`, which has no public URL — `url()` raises. The only route
to a private object is `directory.services.documents.open_evidence()`, which checks
`can_view_evidence` and writes a `DocumentAccessLog` row. In production they are two separate
buckets, not two prefixes in one — a prefix is one bucket-policy edit away from being world
readable and a separate bucket is not.

**A suspended profile explains nothing.** Ask
`backoffice.services.publication.is_publicly_visible()` and render the neutral "not currently
listed" page or a 404. Never the reason — that lives in the audit log, for staff. Publishing it
would be a defamation risk against someone who may be cleared next week.

**Never retype the independence notice.** It is one partial,
`templates/components/_independence_notice.html`, verbatim from `docs/content-compliance.md` §5.
The home page, profile pages, results pages and the contact-reveal interstitial all
`{% include %}` it, so a change to §5 lands in one place. On the home page it sits **above** the
grid: the sentence a visitor needs before reading twelve names and photographs on a
Kiam-branded page cannot be below them.

**Cache primary keys, not rows, and never HTML.** `apps/pages/services/home.py` caches the twelve
chosen ids and re-reads the rows every render. Caching the rows would freeze a practitioner's
own details and the verification badge's wording for a day; caching pickled model instances
would 500 the busiest page on the site after the next migration; and re-applying
`status=PUBLISHED` on the way out means a `bust_cache()` that never fires cannot leave a
suspended listing on the home page.

**`extra={"name": ...}` in a log call raises.** `name` is a reserved `LogRecord` attribute, and
`Logger.makeRecord` raises `KeyError` on a collision — so a log line meant to record a failure
becomes the failure. Two calls in `apps/directory/services/images.py` did this, which turned "a broken
image never breaks the page" into a 500. The suite could not see it: `config/settings/test.py`
sets the root logger to CRITICAL, so `makeRecord` is never reached in a test run.
`apps/directory/tests/test_images.py::test_the_logging_calls_are_actually_emittable` is the guard, and
it is the only test in that module that turns logging on.

**A cached payload's version tracks its MEANING, not just its keys.** Phase 5 added a key to the
browse payload and a cap to the grid selection without bumping `CACHE_VERSION`, so the shape check
passed, the old entry was served, and the live page showed browse counts with no unit next to four
"Paid placement" cards under a promise of three. Both fixes were correct and both were invisible
for as long as the entry lived.

**A link asserts its destination; a text box only suggests one.** `?near=Croydon` was fine as
something a visitor typed — they see the resolved label and can correct it — and wrong as an
authored href, because `geocode.places()` takes the first OS Open Names match and Croydon,
Cambridgeshire is a real place. Authored location links use an outward code.

**Lighthouse and axe scored the home page 100 on accessibility while it had two AA failures.**
Neither tests reflow at 320 px, focus-indicator contrast, or 2.5.8's spacing exception. A
`minmax()` min track cannot shrink below its floor; a focus ring the same colour as the gradient
it is drawn on is not a focus ring.

**An `<ol>` outside `.prose` has no numbers.** Tailwind's preflight sets
`ol, ul, menu { list-style: none }`. Where the sequence is the information this is WCAG 1.3.1, so
`.dir-steps` sets `list-style: decimal` itself rather than relying on a container. Same class of
bug as an unsized heading: kiam-ui sizes only `.ds-section-head__title`, so a plain `<h2>`
renders at body size until this repo's CSS sizes it.
