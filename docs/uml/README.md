<!-- GENERATED FILE — do not edit by hand.
     Regenerate with:  .venv/bin/python .claude/skills/uml/generate_uml.py
     Source of truth is the code; edit the code, then regenerate. -->
# UML

Generated architecture diagrams. **Do not edit `models.md` or
`classes.md`** — they are rebuilt from the code, so an edit there is lost
on the next run and, worse, makes the documentation disagree with the
thing it documents.

| Document | What it covers |
| --- | --- |
| [`models.md`](models.md) | Every model, field and relation, read from Django's app registry. |
| [`classes.md`](classes.md) | Every other class — admin, forms, views, services, middleware, backends, commands. |
| [`workflows.md`](workflows.md) | How a listing moves — publication state machine, lint, dashboard editing, verification, the under-18 gates, public read paths, nightly jobs. **Hand-authored**, not regenerated. |

## Regenerating

```bash
.venv/bin/python .claude/skills/uml/generate_uml.py
```

Or ask Claude to run the `uml` skill.

The output carries no timestamp, so re-running when nothing has changed
produces no diff. A dirty `git status` after a run means the architecture
moved and this is the record of it.

`--check` exits non-zero instead of writing, for a pre-commit hook or CI.

Model apps: `accounts`, `directory`.  
Class apps: `accounts`, `backoffice`, `dashboard`, `directory`, `pages`, `search`, `seo`.

Mermaid renders natively on GitHub and in most Markdown previewers.
