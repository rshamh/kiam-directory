# Kiam Clinic Directory

`directory.kiamclinic.com` — a public directory of independent mental-health practitioners.
Kiam Clinic is the publisher and introducer only. Clients contact and pay practitioners
directly.

Django · PostgreSQL/PostGIS · Redis · Tailwind + HTMX + Alpine · [`kiam-ui`](#kiam-ui)

## Start here

1. `CLAUDE.md` — working rules for this repo
2. `docs/multi-project-architecture.md` — how this sits alongside the main site and rooms
3. `docs/architecture.md` — apps, models, roles, storage
4. `docs/design-system.md` — what `kiam-ui` actually exposes, and the gaps
5. `docs/roadmap.md` — the eight build phases

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
python manage.py runserver
```

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
by*; `accounts/middleware.py` keeps an unverified staff session on the challenge page and
`accounts/access.py` refuses every staff capability until it is answered.

Every role check in the project lives in `accounts/access.py`. Views and templates never inspect
`user.role`.

---

## Two things that are easy to get wrong

**Session cookies stay host-only.** Do not set `SESSION_COOKIE_DOMAIN`. Sharing the cookie
across `.kiamclinic.com` requires a shared `SECRET_KEY`, a shared session store and a shared user
table — i.e. one database — which defeats the project separation entirely
(`docs/multi-project-architecture.md` §3 Option C, §4).

**The two storage backends are never merged.** Headshots go to the public backend; verification
evidence goes to `STORAGES["private"]`, which has no public URL — `url()` raises. The only route
to a private object is `directory.services.documents.signed_url()`, which checks
`can_view_private_evidence` and logs the access. In production they are two separate buckets, not
two prefixes in one.
