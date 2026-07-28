# Phase prompts

Paste one at a time into Claude Code, in order. Do not run two in one session.

Each ends at a gate. When Code stops and prints its gate report, review it, then move on.

| File | Phase |
|---|---|
| `PHASE-0-PROMPT.md` | Foundation |
| `PHASE-1-PROMPT.md` | Data model & taxonomy |
| `PHASE-2-PROMPT.md` | Admin back office |
| `PHASE-3-PROMPT.md` | Public profile |
| `PHASE-4-PROMPT.md` | Search |
| `PHASE-5-PROMPT.md` | Home page |
| `PHASE-6-PROMPT.md` | Practitioner dashboard |
| `PHASE-7-PROMPT.md` | Insights, landing pages, launch prep |

Before Phase 0, make sure these are committed at the repo root:
`CLAUDE.md`, `docs/`, `pyproject.toml`, `.env.example`, `.gitignore`, `.claude/`,
`accounts/models.py`, `accounts/access.py`, `directory/models.py`, `directory/taxonomy.py`,
`directory/services/`.

Also copy in from the main site repo: `.claude/skills/kiam-clinic-design/`.
