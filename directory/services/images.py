"""Headshot renditions — the responsive-image half of the Phase 5 performance budget.

``templates/components/_practitioner_card.html`` has carried this note since
Phase 3:

    No `srcset` yet: `headshot` is a plain ImageField with no rendition pipeline,
    so there is only one file to offer. Tracked for Phase 5 with the home-page
    grid, which has the same need.

This is that pipeline, and it is deliberately the smallest one that works.

**Why it is worth having at all.** `headshot` is whatever the practitioner
uploaded — in practice a phone photo, so 1500–3000px and 200KB–3MB. The largest
box it is ever displayed in is the profile header at 9rem (144 CSS px); the card
and the home-page grid show it at 5rem (80 CSS px). Twelve originals on the home
page is therefore several megabytes to paint an area the size of a postage stamp,
which is the single largest Core Web Vitals cost the page has (``docs/seo.md``,
"Performance": "headshots served responsive and lazy below the fold").

**Three rules, and the second is the one that keeps this safe.**

1. **Deterministic names, derived from the original's.** ``headshots/a.jpg``
   becomes ``headshots/renditions/a-80.jpg``. Django's storage backends never
   overwrite (``file_overwrite=False`` on S3, an auto-suffix on the filesystem and
   in-memory backends), so a replaced headshot arrives under a *new* original name
   and therefore gets new rendition names. There is no cache to bust and no stale
   rendition to serve.

2. **All-or-nothing, and never fatal.** ``srcset`` names candidate URLs the
   browser is entitled to fetch, so a `srcset` listing a rendition that does not
   exist is a broken image — worse than no `srcset` at all. Every failure here
   returns ``""``, so the caller falls back to the plain ``src`` and the original
   file. A missing file, an unreadable JPEG, a storage outage or a Pillow version
   that dislikes the format all end the same way: the page renders.

3. **Generated where the result is cached, not per request.** The only caller is
   ``pages.services.home.grid()``, which is behind a 24-hour cache, so twelve
   headshots are converted once a day rather than on every home-page render. The
   cost of a cache miss is bounded and measured — see that module's docstring.

   There is deliberately **no lock**. Several requests arriving on a cold cache all
   build the same renditions and all but one lose the write race, which is wasted
   CPU but never a wrong file (see ``_write``). Measured: five concurrent requests
   produced 111 losing writes and 36 correct files. A distributed lock would trade
   that for a new failure mode — a crashed lock holder means no grid for the lock's
   lifetime — and the expensive path only happens when a *new* headshot appears. A
   ``bust_cache()`` from a suspension rebuilds the selection but finds every
   rendition already there, so it costs a handful of ``exists()`` calls.

**Why the crop is square and server-side.** ``.dir-practitioner__headshot`` is a
square box with ``object-fit: cover``, so the browser already crops to a centred
square. Doing it here produces exactly the same framing and hands the browser an
image whose intrinsic size *is* its display size — which is what makes the
``width``/``height`` attributes on the card honest and the layout shift zero.

**EXIF is dropped as a side effect, and that is a feature.** A phone photo carries
GPS coordinates. Re-encoding through Pillow without an ``exif`` argument writes
none of it, so a practitioner who photographs themselves at home does not publish
their home's coordinates in the file the whole internet can fetch (golden rule #4,
minimal collection). ``exif_transpose`` is applied first so dropping the
orientation tag does not leave the image sideways.
"""

from __future__ import annotations

import io
import logging
import posixpath

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

from directory.storages import public_storage

logger = logging.getLogger("directory.images")

#: Rendition widths, in CSS pixels of the box they serve.
#:
#: The card and the home-page grid render an 80px box, so these are 1×, 2× and 3×
#: of that. ``sizes="80px"`` (see ``HEADSHOT_SIZES``) is what lets the browser pick
#: one: a 1× phone downloads the 80px file, a 3× phone the 240px file, and neither
#: downloads the 2000px original.
#:
#: Changing this list changes the rendition file names, so existing renditions are
#: simply no longer referenced — nothing breaks, and the new ones are built on the
#: next cache miss. There is no migration and no backfill command to remember.
HEADSHOT_WIDTHS = (80, 160, 240)

#: The ``sizes`` attribute that goes with ``HEADSHOT_WIDTHS``. A fixed box, so a
#: fixed length — no media-query list to keep in step with the CSS.
HEADSHOT_SIZES = "80px"

#: 82 is the usual "no visible artefacts on a photograph" point. On an 80px
#: portrait the difference between 82 and 95 is under a kilobyte and invisible.
JPEG_QUALITY = 82

#: Where renditions live, relative to the public media root. A subdirectory rather
#: than a suffix on the original's path, so `headshots/` stays exactly the set of
#: files a person uploaded.
RENDITION_DIR = "headshots/renditions"


def rendition_name(original_name: str, width: int) -> str:
    """The storage name a rendition of ``original_name`` at ``width`` would have.

    Pure and deterministic — no storage access — so a test can assert the naming
    without writing files, and so two callers can never disagree about where a
    rendition lives.
    """
    stem = posixpath.splitext(posixpath.basename(original_name))[0]
    return f"{RENDITION_DIR}/{stem}-{width}.jpg"


def srcset(image_field, widths=HEADSHOT_WIDTHS) -> str:
    """A ready-to-render ``srcset`` value, or ``""``.

    Builds any rendition that is missing, then returns
    ``"…-80.jpg 80w, …-160.jpg 160w, …"``. Returns ``""`` if there is no image, if
    the original is smaller than the smallest rendition (upscaling a small photo
    wastes bytes to no benefit), or if anything at all goes wrong.

    A string rather than a list, so the template is one attribute and cannot
    accidentally shadow ``forloop`` inside the card's own loop.
    """
    if not image_field or not getattr(image_field, "name", ""):
        return ""

    try:
        return _build(image_field, widths)
    except Exception:  # noqa: BLE001 — see rule 2 in the module docstring
        # `image` and NOT `name`: `name` is a reserved LogRecord attribute (the
        # logger's own name), and `logging.Logger.makeRecord` raises KeyError on an
        # `extra` key that collides with one. Both log calls in this module used it
        # at first, which turned the "never fatal" promise into a KeyError escaping
        # `srcset` — on a page whose whole point is that a broken image does not
        # break it.
        #
        # The suite could not see it: `config/settings/test.py` sets the root logger
        # to CRITICAL, so `isEnabledFor(INFO)` is False, `makeRecord` is never
        # reached and the reserved-key collision never happens. Green suite, 500 on
        # the real server. This is CLAUDE.md's "run the real thing" rule again, and
        # `test_the_logging_calls_are_actually_emittable` is the guard.
        logger.exception("images.srcset_failed", extra={"image": getattr(image_field, "name", "")})
        return ""


def _build(image_field, widths) -> str:
    storage = public_storage()
    original_name = image_field.name

    wanted = sorted({int(w) for w in widths if int(w) > 0})
    missing = [w for w in wanted if not storage.exists(rendition_name(original_name, w))]

    if missing:
        # One decode for every rendition, not one per width. The original may be a
        # 3000px photo and decoding it three times is three times the memory churn
        # on a request that is already doing twelve of these.
        with storage.open(original_name, "rb") as handle:
            source = ImageOps.exif_transpose(Image.open(handle))
            source = source.convert("RGB")

            # Never upscale. A 60px avatar rendered into a 240px file is a bigger
            # download than the original for a blurrier picture, and the browser
            # would dutifully pick it on a 3× screen.
            shortest = min(source.size)
            for width in missing:
                if width > shortest:
                    continue
                _write(storage, source, original_name, width)

    sources = [
        f"{storage.url(rendition_name(original_name, w))} {w}w"
        for w in wanted
        if storage.exists(rendition_name(original_name, w))
    ]
    return ", ".join(sources)


def _write(storage, source: Image.Image, original_name: str, width: int) -> None:
    """One rendition: centre-cropped to a square, resized, saved as JPEG.

    ``ImageOps.fit`` crops to the target aspect ratio and resizes in one step, with
    centred cropping — the same framing ``object-fit: cover`` produces on the
    square box the card and the grid render.
    """
    square = ImageOps.fit(source, (width, width), method=Image.LANCZOS, centering=(0.5, 0.5))

    buffer = io.BytesIO()
    # `optimize` costs a little CPU once and saves bytes on every request for the
    # lifetime of the file. `progressive` renders a low-detail pass first, which is
    # what a throttled connection sees instead of a blank box.
    square.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)

    target = rendition_name(original_name, width)
    saved = storage.save(target, ContentFile(buffer.getvalue()))

    if saved != target:
        # We lost a race. Django's storage backends never overwrite: another request
        # wrote `target` between our `exists()` check and this line, so the backend
        # handed back a suffixed name instead. The srcset is rebuilt from `exists()`
        # and will point at `target`, so this file is an orphan nothing will ever
        # request — delete it rather than leave it on the bucket for the life of the
        # listing.
        #
        # Not hypothetical, and not rare: this was measured. `runserver` is threaded
        # and Lighthouse loads the page twice in quick succession, which produced a
        # second copy of all 69 renditions in development. Under gunicorn with
        # several workers, any burst of traffic on a cold grid cache does the same.
        logger.info("images.rendition_race", extra={"rendition": target})
        storage.delete(saved)
