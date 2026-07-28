"""``collectstatic``, with kiam-ui's CSS *source* excluded by default.

kiam-ui ships its authored CSS at ``kiam_ui/static/kiam_ui/css/src/``. That
directory is **build input**, not an asset: ``theme.css`` is a Tailwind v4
preset that opens with ``@import "tailwindcss"`` and resolves its values at
runtime from the compiled stylesheet. It is not a servable file and was never
meant to be one.

But it lives under ``static/``, so ``collectstatic`` picks it up, and
``ManifestStaticFilesStorage`` — the standard production choice, and what
WhiteNoise recommends — post-processes every ``.css`` file it collects and tries
to resolve each ``@import``. ``tailwindcss`` is a package name, not a path, so
the whole command dies:

    whitenoise.storage.MissingFileError: The file 'kiam_ui/css/src/tailwindcss'
    could not be found

Passing ``--ignore`` on the command line fixes it, which means it works right up
until the first person who runs ``collectstatic`` without remembering the flag —
and that person is usually a deploy script. Overriding the default here makes it
work by construction. An explicit ``--ignore`` on the command line still adds to
these rather than replacing them.

This is a kiam-ui packaging problem, recorded as a Gap in docs/design-system.md.
The upstream fix is to ship ``css/src/`` outside the package's static tree; when
that lands, this override can go.
"""

from django.contrib.staticfiles.management.commands.collectstatic import (
    Command as CollectStaticCommand,
)

#: Paths under the static tree that are build input rather than assets.
DEFAULT_IGNORE_PATTERNS = ["src"]


class Command(CollectStaticCommand):
    def handle(self, **options):
        options["ignore_patterns"] = list(options.get("ignore_patterns") or []) + list(
            DEFAULT_IGNORE_PATTERNS
        )
        return super().handle(**options)
