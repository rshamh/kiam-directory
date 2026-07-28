# Multi-project architecture

Kiam runs as **three independent codebases** on subdomains. This file lives in **all three
repos** and is the source of truth for what they share, what they must not share, and how
they stay consistent. Read it before starting work in any of them.

| Project | Domain | Stack | Notes |
|---|---|---|---|
| **Main site** | kiamclinic.com | Wagtail + Django | The brand + content site. Owns the design system. |
| **Room rental** | rooms.kiamclinic.com | Django (+ Stripe) | LatePoint-style booking. Own repo/DB. |
| **Directory** | directory.kiamclinic.com | Django | Practitioner directory, introducer model. Own repo/DB. |

Separation is deliberate: it isolates compliance risk (the directory's legal review is still
open), lets each ship on its own timeline, and — for the directory — **reinforces the
independence framing** that clients must clearly perceive.

---

## 1. What is shared, and what is not

**Shared:** visual language (tokens, components, header/footer chrome), composition rules,
compliance rules, contact details (as content, not a live API), and the Claude Code working
practices (CLAUDE.md conventions, review subagents, phase gates).

**NOT shared:** databases, user tables, sessions, business logic, deploy stacks. Each project
is independently deployable and independently down-able.

---

## 2. Keeping them visually consistent

Three mechanisms, in order of how tightly they bind:

### a. The design system skill (copy into each repo)

`.claude/skills/kiam-clinic-design/` — copy the same folder into all three repos. It is the
authoritative source for colour, type, spacing, effects, icons, logo, and components. Also
copy `docs/design-system.md`, `docs/page-composition.md`, and this file.

### b. A shared UI package (`kiam-ui`) — recommended once the main site is stable

Copying templates into three repos guarantees drift: the header changes on the main site and
the other two silently fall behind. Extract the genuinely shared chrome into a small private
Python package installed from Git — no PyPI publishing required:

```
pip install git+https://github.com/<org>/kiam-ui@v1.2.0
```

The package contains: compiled design-system CSS (built from the tokens), the base template,
the three-layer header, the footer, the accessibility preference panel, and the core
component partials (button, pill, card, disclaimer, field, CTA banner). It contains **no
business logic and no models**.

Consumers override what they must via template blocks and a small settings dict (site name,
nav items, which CTAs to show, subdomain URLs).

**Version it and pin it.** Each project upgrades deliberately (`@v1.2.0` → `@v1.3.0`), so a
main-site change never silently breaks booking. Changelog every release.

**Timing matters: do NOT extract `kiam-ui` until the main site's composition refinement pass
is finished and its header/base template has settled.** Extracting a moving target creates
churn in three repos instead of one.

### c. Per-repo CLAUDE.md

Each repo gets its own `CLAUDE.md` with the same golden rules (SEO on every page, no
prescription-only medication names, accessibility built in natively, RTL-ready, GDPR by
design, tests per app, commit + push every change) plus that project's specifics.

---

## 3. Shared login across the three projects

**Recommendation: start with separate authentication per project. Adopt SSO only when the
overlap justifies it.**

Look at who actually logs in where:

| Role | Main | Rooms | Directory |
|---|---|---|---|
| Clinic admin | ✅ | ✅ | ✅ |
| Content supervisor / board / facilitator | ✅ | — | — |
| Room-rental manager | — | ✅ | — |
| Directory practitioner | — | — | ✅ |
| Patients | never (Semble) | never | never |

Only **clinic admin** genuinely spans all three — a handful of people. Building SSO to save
two extra passwords for two or three staff is poor value *now*, and it adds a hard runtime
dependency: if the identity provider is down, nobody can log into anything.

### Option A — Separate auth per project (recommended to start)

Each project has its own `User` model and its own login. Simplest, fully isolated, smallest
blast radius if one project is compromised. Cost: clinic admin has three accounts. Mitigate
with a password manager and consistent 2FA policy across all three.

### Option B — Central SSO via OpenID Connect (adopt when overlap grows)

The **main site becomes the identity provider** (e.g. `django-oauth-toolkit` / an OIDC
provider app); rooms and directory become OIDC clients. One login, one 2FA enrolment, one
place to revoke access when someone leaves — which is a genuine governance benefit for a
regulated clinic.

Design for this now so it isn't a rewrite later: keep each project's `User` model **thin**
(email as identifier, no business data on the user), keep role checks in a single
`access.py` per project, and never assume user IDs match across projects.

Migration path: add the provider to the main site, add an OIDC login button to the other two
alongside local login, link accounts by verified email, then retire local logins.

### Option C — Shared session cookie on `.kiamclinic.com` — NOT recommended

Requires a shared `SECRET_KEY`, shared session store, and a **shared user table** — i.e. one
database. That defeats project separation, couples deploys, and means a compromise anywhere
is a compromise everywhere. Do not do this.

---

## 4. Cross-subdomain concerns (get these right in all three)

- **Cookie consent** should be stored on the parent domain `.kiamclinic.com` so a visitor
  consents once and it is honoured across all three. Consent categories must match.
- **Analytics** (when added) uses one property with subdomain tracking, and fires only after
  consent, everywhere.
- **CSP**: each project allowlists what it actually embeds (e.g. the Semble booking iframe on
  the main site; Stripe on rooms). Do not blanket-allow across subdomains.
- **Session cookies stay host-only** (do NOT set `SESSION_COOKIE_DOMAIN=.kiamclinic.com`)
  unless and until Option B is adopted — host-only is what keeps the projects isolated.
- **Emergency disclaimer + accessibility panel** appear on all three (they come with `kiam-ui`).

---

## 5. SEO across subdomains

Search engines treat subdomains as **separate sites**. So:

- Each project has its **own** `robots.txt`, `sitemap.xml`, and `llms.txt`.
- Each emits its own `MedicalClinic`/`LocalBusiness` or `Organization` JSON-LD with the same
  NAP (13 Worple Road, Epsom, Surrey KT18 5EP · 01372 660580 · enquiries@kiamclinic.com).
- **Cross-link deliberately**: main site → rooms and directory from the relevant service
  pages; both back to the main site. This is how authority flows between them.
- Canonicals point within the subdomain that owns the content — never across.
- **Directory exception**: its independence framing must be visually and textually obvious.
  Do not imply listed practitioners are Kiam clinicians. Keep its branding clearly related
  but distinct.

---

## 6. Deployment

One VPS can host all three. Keep them as **separate Docker Compose stacks with separate
databases**, fronted by a single nginx routing by subdomain, with TLS per subdomain
(wildcard or individual certs). Separate Sentry projects. Back up each database separately.

DNS: `rooms` and `directory` as A/CNAME records pointing at the same host.

---

## 7. Working practice with Claude

- **One conversation per project** (inside the same Claude Project, so context is shared).
  Point each new conversation at this file plus that repo's `CLAUDE.md` and `docs/`.
- The **repo is the source of truth**, not chat history. Before asking for a change to a doc,
  paste the current version — Claude Code edits these files as it works and they drift.
- Each project runs its own phased roadmap with review gates, its own Notion tasks, and the
  same review subagents (seo, a11y, compliance).
