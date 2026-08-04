"""Generate the project's UML documentation from the source of truth: the code.

Two passes, deliberately different in kind:

* **Models** come from Django's own app registry, not from parsing text. The registry
  is what the ORM actually resolved — a ``ForeignKey`` written as a string, a swapped
  ``AUTH_USER_MODEL``, an inherited abstract base and an implicit ``id`` all appear as
  they really are. ``django.setup()`` touches no database, so this runs anywhere the
  project imports.
* **Everything that is not a model** comes from ``ast``. Importing every admin, form,
  view and service module to read its classes would execute module-level code for the
  sake of a diagram; parsing does not. The cost is that a base class is recorded as the
  name written at the class statement, which is what a reader wants anyway.

Output is deterministic and carries no timestamp: re-running with nothing changed
produces no diff, so a noisy `git status` after a run means the model layer moved.

    .venv/bin/python .claude/skills/uml/generate_uml.py [--output docs/uml] [--check]
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
APPS_DIR = REPO_ROOT / "apps"

# Only these apps are diagrammed. Everything else in INSTALLED_APPS is Django's or a
# third party's, and belongs in their documentation rather than ours.
LOCAL_PREFIX = "apps."

# Modules whose classes the AST pass skips, and why:
#   models.py     — covered by the model pass, in far more detail
#   migrations/   — generated, and a diagram of them is a diagram of history
#   tests/        — test classes are not part of the architecture
SKIP_DIR_PARTS = {"migrations", "__pycache__", "tests"}
SKIP_FILENAMES = {"models.py"}

BANNER = (
    "<!-- GENERATED FILE — do not edit by hand.\n"
    "     Regenerate with:  .venv/bin/python .claude/skills/uml/generate_uml.py\n"
    "     Source of truth is the code; edit the code, then regenerate. -->\n"
)


# --------------------------------------------------------------------------- #
# Model pass — Django app registry
# --------------------------------------------------------------------------- #


def setup_django() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
    import django

    django.setup()


def node_id(model, home_label: str) -> str:
    """Mermaid class id for ``model`` as seen from ``home_label``'s diagram.

    Mermaid ids cannot contain a dot, and two apps may legitimately hold a model of
    the same name, so a foreign model is namespaced with its app label.
    """
    name = model.__name__
    label = model._meta.app_label
    return name if label == home_label else f"{label}_{name}"


def field_line(field) -> str:
    """One member line for a concrete field.

    Markers stay alphabetic: mermaid treats ``(`` as the start of a method signature
    and several punctuation marks as syntax, so ``PK``/``U``/``idx``/``null`` are
    words rather than symbols.
    """
    kind = field.get_internal_type()
    if field.is_relation and field.related_model is not None:
        kind = field.related_model.__name__

    marks = []
    if field.primary_key:
        marks.append("PK")
    if field.unique and not field.primary_key:
        marks.append("U")
    if getattr(field, "db_index", False) and not field.unique:
        marks.append("idx")
    if field.null:
        marks.append("null")

    suffix = f" {' '.join(marks)}" if marks else ""
    return f"    +{field.name} : {kind}{suffix}"


def enum_classes(model_module) -> dict:
    """``TextChoices``/``IntegerChoices`` classes defined in a models module."""
    from django.db.models import Choices

    found = {}
    for name in dir(model_module):
        obj = getattr(model_module, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, Choices)
            and obj is not Choices
            and obj.__module__ == model_module.__name__
        ):
            found[name] = obj
    return found


def abstract_bases(model) -> list:
    """Abstract model parents, which the registry does not list as models."""
    from django.db.models import Model

    out = []
    for base in model.__mro__[1:]:
        if not isinstance(base, type) or not issubclass(base, Model):
            continue
        if base is Model:
            break
        meta = getattr(base, "_meta", None)
        if meta is not None and meta.abstract:
            out.append(base)
    return out


def build_models_doc() -> str:
    import importlib

    from django.apps import apps as django_apps

    lines = [BANNER, "# UML — data model\n"]
    lines.append(
        "Every class, field and relation below is read out of Django's app registry, so "
        "this is what the ORM resolved rather than what a `models.py` appears to say.\n"
    )
    lines.append(
        "Field markers: `PK` primary key · `U` unique · `idx` indexed · `null` nullable. "
        "A relation's type column names the target model; the arrow carries the "
        "cardinality.\n"
    )

    configs = [c for c in django_apps.get_app_configs() if c.name.startswith(LOCAL_PREFIX)]
    configs = [c for c in configs if list(c.get_models())]
    configs.sort(key=lambda c: c.label)

    lines.append("**Apps with models:** " + ", ".join(f"`{c.label}`" for c in configs) + "\n")

    cross_app: list[str] = []

    for config in configs:
        label = config.label
        models = sorted(config.get_models(), key=lambda m: m.__name__)
        module = importlib.import_module(f"{config.name}.models")
        enums = enum_classes(module)

        lines.append(f"\n## `{label}`\n")
        lines.append("```mermaid")
        lines.append("classDiagram")
        lines.append("    direction LR")

        drawn_foreign: set[str] = set()
        drawn_abstract: set[str] = set()
        used_enums: dict[str, object] = {}
        edges: list[str] = []

        for model in models:
            mid = node_id(model, label)
            meta = model._meta
            lines.append(f"    class {mid} {{")
            if meta.proxy:
                lines.append("    <<proxy>>")
            for field in meta.concrete_fields:
                lines.append(field_line(field))
            for field in meta.local_many_to_many:
                lines.append(f"    +{field.name} : {field.related_model.__name__} many")
            lines.append("    }")

            for base in abstract_bases(model):
                bid = base.__name__
                if bid not in drawn_abstract:
                    drawn_abstract.add(bid)
                edges.append(f"    {bid} <|-- {mid}")

            for field in list(meta.concrete_fields) + list(meta.local_many_to_many):
                target = field.related_model
                if target is None or not field.is_relation:
                    continue
                tid = node_id(target, label)
                if target._meta.app_label != label and tid not in drawn_foreign:
                    drawn_foreign.add(tid)
                if field.many_to_many:
                    edges.append(f'    {mid} "*" -- "*" {tid} : {field.name}')
                elif field.one_to_one:
                    edges.append(f'    {mid} "1" --> "1" {tid} : {field.name}')
                else:
                    edges.append(f'    {mid} "*" --> "1" {tid} : {field.name}')
                if target._meta.app_label != label:
                    cross_app.append(
                        f"| `{label}.{model.__name__}.{field.name}` | "
                        f"`{target._meta.label}` | {field.get_internal_type()} |"
                    )

                # Choice sets are part of the type, so the enum belongs on the diagram.
            for field in meta.concrete_fields:
                choices = getattr(field, "choices", None)
                if not choices:
                    continue
                for ename, enum in enums.items():
                    if list(enum.choices) == list(choices):
                        used_enums[ename] = enum
                        edges.append(f"    {mid} ..> {ename} : {field.name}")
                        break

        for name in sorted(drawn_abstract):
            lines.append(f"    class {name} {{")
            lines.append("    <<abstract>>")
            lines.append("    }")

        for name in sorted(drawn_foreign):
            lines.append(f"    class {name} {{")
            lines.append("    <<external>>")
            lines.append("    }")

        for ename in sorted(used_enums):
            lines.append(f"    class {ename} {{")
            lines.append("    <<enumeration>>")
            for value, _display in used_enums[ename].choices:
                lines.append(f"    {str(value).replace(' ', '_')}")
            lines.append("    }")

        lines.extend(sorted(set(edges)))
        lines.append("```\n")

        lines.append(f"### `{label}` models\n")
        lines.append("| Model | Table | Purpose |")
        lines.append("| --- | --- | --- |")
        for model in models:
            doc = (model.__doc__ or "").strip().splitlines()
            first = doc[0].strip() if doc else ""
            if first.startswith(f"{model.__name__}("):  # Django's auto-generated docstring
                first = ""
            lines.append(f"| `{model.__name__}` | `{model._meta.db_table}` | {escape_cell(first)} |")
        lines.append("")

    lines.append("\n## Cross-app relations\n")
    if cross_app:
        lines.append(
            "Each row is a place where one app's table points at another's. These are the "
            "seams: a change to the target is a change to every source listed here.\n"
        )
        lines.append("| From | To | Kind |")
        lines.append("| --- | --- | --- |")
        lines.extend(sorted(set(cross_app)))
    else:
        lines.append("None — every relation stays inside its own app.")
    lines.append("")

    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Class pass — AST over apps/
# --------------------------------------------------------------------------- #


def base_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{base_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return base_name(node.value)
    if isinstance(node, ast.Call):
        return base_name(node.func)
    return "?"


def escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def collect_classes() -> dict[str, list[dict]]:
    """Top-level classes per app, in file then source order."""
    by_app: dict[str, list[dict]] = defaultdict(list)

    for path in sorted(APPS_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if set(rel.parts) & SKIP_DIR_PARTS or path.name in SKIP_FILENAMES:
            continue
        if path.name.startswith("test_"):
            continue
        app = rel.parts[1]

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # a broken file should be loud, not silently absent
            print(f"warning: could not parse {rel}: {exc}", file=sys.stderr)
            continue

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            doc = ast.get_docstring(node) or ""
            methods = [
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            ]
            by_app[app].append(
                {
                    "name": node.name,
                    "bases": [base_name(b) for b in node.bases],
                    "module": str(rel),
                    "line": node.lineno,
                    "doc": doc.strip().splitlines()[0].strip() if doc.strip() else "",
                    "methods": methods,
                }
            )

    return by_app


def build_classes_doc(by_app: dict[str, list[dict]]) -> str:
    lines = [BANNER, "# UML — classes outside the data model\n"]
    lines.append(
        "Admin, forms, views, services, middleware, backends, storages and commands. "
        "Model classes are not repeated here — they are in "
        "[`models.md`](models.md).\n"
    )
    lines.append(
        "Read from the class statement itself, so a base class is the name written in the "
        "source. Classes defined in this repo are drawn solid; a base that comes from "
        "Django, a third party or another module is marked `<<external>>`.\n"
    )

    for app in sorted(by_app):
        classes = by_app[app]
        if not classes:
            continue
        lines.append(f"\n## `{app}`\n")

        # Two modules in one app may each define a class of the same name — this repo
        # has two `RateLimited` exceptions. Mermaid would silently merge them into one
        # node and draw a class that does not exist, so a repeated name is suffixed
        # with the module it comes from.
        counts: dict[str, int] = defaultdict(int)
        for cls in classes:
            counts[cls["name"]] += 1
        for cls in classes:
            stem = Path(cls["module"]).stem
            cls["id"] = cls["name"] if counts[cls["name"]] == 1 else f"{cls['name']}_{stem}"

        # A base resolves to a class in this app only when that name is unambiguous;
        # otherwise it is drawn as external rather than guessed at.
        local = {c["name"]: c["id"] for c in classes if counts[c["name"]] == 1}
        edges: list[str] = []
        external: set[str] = set()
        for cls in classes:
            for base in cls["bases"]:
                if base in ("object", "?"):
                    continue
                if base in local:
                    bid = local[base]
                else:
                    bid = base.replace(".", "_")
                    external.add(base)
                edges.append(f"    {bid} <|-- {cls['id']}")

        lines.append("```mermaid")
        lines.append("classDiagram")
        lines.append("    direction LR")
        for cls in classes:
            lines.append(f"    class {cls['id']} {{")
            for method in cls["methods"][:12]:
                lines.append(f"    +{method}()")
            lines.append("    }")
        for base in sorted(external):
            lines.append(f"    class {base.replace('.', '_')} {{")
            lines.append("    <<external>>")
            lines.append("    }")
            if base != base.replace(".", "_"):
                lines.append(f'    note for {base.replace(".", "_")} "{base}"')
        lines.extend(sorted(set(edges)))
        lines.append("```\n")

        lines.append("| Class | Inherits | Defined in | Purpose |")
        lines.append("| --- | --- | --- | --- |")
        for cls in sorted(classes, key=lambda c: (c["module"], c["line"])):
            bases = ", ".join(f"`{b}`" for b in cls["bases"]) or "—"
            lines.append(
                f"| `{cls['name']}` | {bases} | "
                f"[`{cls['module']}`]({relative_from_docs(cls['module'])}#L{cls['line']}) | "
                f"{escape_cell(cls['doc'])} |"
            )
        lines.append("")

    return "\n".join(lines)


def relative_from_docs(module: str) -> str:
    """Link from ``docs/uml/*.md`` back to a repository path."""
    return "../../" + module


def build_index(model_apps: list[str], class_apps: list[str]) -> str:
    return BANNER + "\n".join(
        [
            "# UML",
            "",
            "Generated architecture diagrams. **Do not edit `models.md` or",
            "`classes.md`** — they are rebuilt from the code, so an edit there is lost",
            "on the next run and, worse, makes the documentation disagree with the",
            "thing it documents.",
            "",
            "| Document | What it covers |",
            "| --- | --- |",
            "| [`models.md`](models.md) | Every model, field and relation, read from "
            "Django's app registry. |",
            "| [`classes.md`](classes.md) | Every other class — admin, forms, views, "
            "services, middleware, backends, commands. |",
            "| [`workflows.md`](workflows.md) | How a listing moves — publication state "
            "machine, lint, dashboard editing, verification, the under-18 gates, public "
            "read paths, nightly jobs. **Hand-authored**, not regenerated. |",
            "",
            "## Regenerating",
            "",
            "```bash",
            ".venv/bin/python .claude/skills/uml/generate_uml.py",
            "```",
            "",
            "Or ask Claude to run the `uml` skill.",
            "",
            "The output carries no timestamp, so re-running when nothing has changed",
            "produces no diff. A dirty `git status` after a run means the architecture",
            "moved and this is the record of it.",
            "",
            "`--check` exits non-zero instead of writing, for a pre-commit hook or CI.",
            "",
            f"Model apps: {', '.join(f'`{a}`' for a in model_apps)}.  ",
            f"Class apps: {', '.join(f'`{a}`' for a in class_apps)}.",
            "",
            "Mermaid renders natively on GitHub and in most Markdown previewers.",
            "",
        ]
    )


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default="docs/uml",
        help="Directory to write into, relative to the repository root.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Write nothing; exit 1 if the generated output differs from what is on disk.",
    )
    args = parser.parse_args()

    setup_django()

    from django.apps import apps as django_apps

    models_doc = build_models_doc()
    by_app = collect_classes()
    classes_doc = build_classes_doc(by_app)

    model_apps = sorted(
        c.label
        for c in django_apps.get_app_configs()
        if c.name.startswith(LOCAL_PREFIX) and list(c.get_models())
    )
    index_doc = build_index(model_apps, sorted(by_app))

    out_dir = REPO_ROOT / args.output
    wanted = {
        out_dir / "README.md": index_doc,
        out_dir / "models.md": models_doc,
        out_dir / "classes.md": classes_doc,
    }

    if args.check:
        stale = [
            path
            for path, text in wanted.items()
            if not path.exists() or path.read_text(encoding="utf-8") != text
        ]
        if stale:
            for path in stale:
                print(f"out of date: {path.relative_to(REPO_ROOT)}", file=sys.stderr)
            print(
                "Run: .venv/bin/python .claude/skills/uml/generate_uml.py",
                file=sys.stderr,
            )
            return 1
        print("UML documentation is up to date.")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in wanted.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(REPO_ROOT)}")

    total_models = sum(
        len(list(c.get_models())) for c in django_apps.get_app_configs() if c.name.startswith(LOCAL_PREFIX)
    )
    print(f"{total_models} models, {sum(len(v) for v in by_app.values())} other classes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
