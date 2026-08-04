---
name: uml
description: Regenerate the project's UML diagrams — every Django model with its fields and relations, plus every other class (admin, forms, views, services, middleware, backends, commands) with its inheritance. Use when asked to create, update or refresh the UML, class diagrams, ER diagram or model diagram, and after any migration, new app, new model or new service class.
---

# UML

Rebuilds `docs/uml/` from the code. Nothing in it is written by hand.

```bash
.venv/bin/python .claude/skills/uml/generate_uml.py
```

That is the whole task in the ordinary case. The rest of this file is what to do with the
result and what the generator will not tell you.

## What it produces

| File | Contents |
| --- | --- |
| `docs/uml/README.md` | Index and the regeneration command. |
| `docs/uml/models.md` | One Mermaid `classDiagram` per app with models, every concrete field and M2M, abstract bases, `TextChoices` enums, external models (`auth.Group`, …), plus a per-app model table and a **cross-app relations** table. |
| `docs/uml/classes.md` | One diagram and one table per app for every class that is not a model — admin, forms, views, services, middleware, backends, storages, factories, apps configs, commands — with base classes, defining module, line link and docstring summary. |

Mermaid renders natively on GitHub and in the Claude Code preview.

## How it reads the code, and why that matters

* **Models come from Django's app registry**, not from parsing `models.py`. `django.setup()` runs
  under `config.settings.test` and touches no database. So a string `ForeignKey`, the swapped
  `AUTH_USER_MODEL`, an inherited abstract base and an implicit `id` all appear as the ORM
  actually resolved them — the diagram cannot drift from the schema by being read wrong.
* **Everything else comes from `ast`.** Importing every admin, form and service module to list its
  classes would execute module-level code for the sake of a diagram. The trade is that a base
  class is recorded as the name written at the `class` statement, which is what a reader wants;
  an aliased import shows its alias.
* **Scope is `apps/` only.** Every app under `apps/` is picked up automatically — a new app needs
  no change here. `migrations/`, `tests/`, `test_*.py` and `models.py` are skipped in the class
  pass (models are covered in far more detail by the model pass).

## After running

1. `git diff docs/uml/` and **read it**. The output carries no timestamp, so a clean run on
   unchanged code produces no diff at all. A diff is a real architectural change, and it is worth
   asking whether it was the intended one — an unexpected new `ForeignKey` into `directory` or a
   new class inheriting `ModelAdmin` is exactly the kind of thing this catches.
2. Pay particular attention to the **cross-app relations** table in `models.md`. Those rows are
   the seams between apps; a new one is a coupling decision, not a detail.
3. Commit it with the change that caused it, not on its own.

`--check` writes nothing and exits 1 if the docs are stale, naming the files. It is there for a
pre-commit hook or CI:

```bash
.venv/bin/python .claude/skills/uml/generate_uml.py --check
```

`--output <dir>` writes somewhere other than `docs/uml`.

## What it will not tell you

The generator describes **structure**. It says nothing about the rules in `CLAUDE.md` that the
structure exists to enforce, and a diagram is a bad place to learn them:

* The five computed verification fields look like ordinary columns on `Practitioner`. Only
  `directory.services.verification` may write them.
* `minor_work_status` looks like one field. It is gated in **two** places, and the diagram shows
  neither.
* An arrow from `Document` to `Practitioner` says nothing about `open_evidence()` being the only
  door.

If a diagram is being produced for someone reading it as documentation of *how the system
behaves*, point them at `docs/architecture.md` and `CLAUDE.md` alongside it.

## If it fails

* `ModuleNotFoundError` / `ImproperlyConfigured` — use the project venv (`.venv/bin/python`); the
  script sets `DJANGO_SETTINGS_MODULE=config.settings.test` itself if it is unset.
* GEOS/GDAL errors — GeoDjango cannot find its libraries. Same environment the test suite needs;
  fix it there, not here.
* `warning: could not parse …` on stderr — a genuinely broken source file, printed rather than
  swallowed. Fix the file; the class pass skipped it.

## Changing the generator

Two behaviours are load-bearing and should not be simplified away:

* **No timestamp in the output.** It is what makes "no diff means nothing changed" true, and that
  property is the whole reason `--check` works.
* **Duplicate class names within an app are suffixed with their module.** `accounts` defines two
  different `RateLimited` exceptions; Mermaid merges same-named nodes silently, so without the
  suffix the diagram would draw one class that does not exist.
