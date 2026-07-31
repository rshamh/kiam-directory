"""``directory.services.images`` — headshot renditions.

The two rules worth pinning are the ones that keep a `srcset` from making things
worse: it is never partial (a candidate URL that 404s is a broken image), and it
never upscales (a bigger file for a blurrier picture).
"""

from __future__ import annotations

import io
import logging

import pytest
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from PIL import Image

from apps.directory.factories import PractitionerFactory
from apps.directory.services import images

pytestmark = pytest.mark.django_db


def photo(width: int = 1200, height: int = 1600, mode: str = "RGB") -> bytes:
    """A real image, because Pillow is the thing under test.

    Portrait by default: a phone photo of a person, which is what a practitioner
    actually uploads, and the shape that makes the square crop visible.
    """
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color=(120, 160, 150)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def with_headshot():
    def make(data: bytes, name: str = "portrait.jpg"):
        practitioner = PractitionerFactory(published=True, slug="has-a-photo")
        practitioner.headshot.save(name, ContentFile(data), save=True)
        return practitioner

    return make


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_rendition_names_are_derived_from_the_original():
    """Deterministic and pure — no storage access — so two callers cannot disagree
    about where a rendition lives."""
    assert images.rendition_name("headshots/aisha.jpg", 160) == "headshots/renditions/aisha-160.jpg"
    assert images.rendition_name("headshots/aisha.png", 80) == "headshots/renditions/aisha-80.jpg"


def test_a_replaced_headshot_gets_new_rendition_names(with_headshot):
    """Storage backends never overwrite, so a second upload lands under a new
    original name — which is what means there is no stale rendition to bust."""
    practitioner = with_headshot(photo(), name="same-name.jpg")
    first = practitioner.headshot.name

    practitioner.headshot.save("same-name.jpg", ContentFile(photo()), save=True)

    assert practitioner.headshot.name != first
    assert images.rendition_name(practitioner.headshot.name, 80) != images.rendition_name(first, 80)


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def test_a_srcset_is_built_for_every_width(with_headshot):
    practitioner = with_headshot(photo())

    value = images.srcset(practitioner.headshot)

    for width in images.HEADSHOT_WIDTHS:
        assert f"-{width}.jpg {width}w" in value
    assert value.count(",") == len(images.HEADSHOT_WIDTHS) - 1


def test_renditions_are_square_and_the_size_they_claim(with_headshot):
    """The card renders an 80px square with `object-fit: cover`, so the crop is done
    here and the `width`/`height` attributes become honest."""
    practitioner = with_headshot(photo(1200, 1600))
    images.srcset(practitioner.headshot)

    storage = storages["default"]
    with storage.open(images.rendition_name(practitioner.headshot.name, 160), "rb") as handle:
        assert Image.open(handle).size == (160, 160)


def test_a_rendition_is_very_much_smaller_than_the_original(with_headshot):
    """The whole point: twelve of these on the home page instead of twelve phone
    photos is the largest Core Web Vitals saving the page has."""
    original = photo(2000, 2600)
    practitioner = with_headshot(original)
    images.srcset(practitioner.headshot)

    storage = storages["default"]
    rendered = storage.size(images.rendition_name(practitioner.headshot.name, 160))

    assert rendered < len(original) / 5


def test_renditions_are_built_once_and_then_reused(with_headshot):
    practitioner = with_headshot(photo())
    first = images.srcset(practitioner.headshot)

    calls = []
    original_save = storages["default"].save

    def counting_save(name, content, **kwargs):
        calls.append(name)
        return original_save(name, content, **kwargs)

    storages["default"].save = counting_save
    try:
        assert images.srcset(practitioner.headshot) == first
    finally:
        storages["default"].save = original_save

    assert calls == []


def test_losing_a_write_race_leaves_no_orphan_file(with_headshot):
    """Django's storage backends never overwrite, so a second writer gets a suffixed
    name — and that file is one nothing will ever request, because the srcset is
    rebuilt from the canonical name.

    Measured, not theorised: `runserver` is threaded and Lighthouse loads the page
    twice in quick succession, which left a second copy of all 69 renditions in the
    development media directory. Under gunicorn with several workers, any burst on a
    cold grid cache does the same.
    """
    practitioner = with_headshot(photo())
    storage = storages["default"]
    target = images.rendition_name(practitioner.headshot.name, 80)

    # Stand in for the other request: the file is already there, but our own
    # `exists()` pre-check misses it — which is exactly the window the race lives in.
    # The lie is told once; every later call, including the one that assembles the
    # srcset, sees the truth.
    original_exists = storage.exists
    lied = []

    def exists(name):
        if name == target and not lied:
            lied.append(name)
            return False
        return original_exists(name)

    storage.save(target, ContentFile(b"the other request got here first"))
    storage.exists = exists
    try:
        value = images.srcset(practitioner.headshot)
    finally:
        storage.exists = original_exists

    assert f"{storage.url(target)} 80w" in value

    basename = target.rsplit("/", 1)[-1]
    stem = basename.removesuffix(".jpg")
    orphans = [
        name
        for name in storage.listdir(images.RENDITION_DIR)[1]
        if name.startswith(stem) and name != basename
    ]
    assert orphans == [], f"a losing race left {orphans} behind"


def test_a_small_photo_is_never_upscaled(with_headshot):
    """A 100px avatar rendered into a 240px file is a bigger download for a blurrier
    picture — and the browser would dutifully pick it on a 3× screen."""
    practitioner = with_headshot(photo(100, 100))

    value = images.srcset(practitioner.headshot)

    assert "-80.jpg 80w" in value
    assert "-240.jpg" not in value


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


def test_no_headshot_is_an_empty_srcset_not_an_error():
    practitioner = PractitionerFactory(published=True, slug="no-photo")

    assert images.srcset(practitioner.headshot) == ""


def test_an_unreadable_file_gives_an_empty_srcset(with_headshot):
    """All-or-nothing. A `srcset` listing a rendition that does not exist is a
    broken image, which is worse than no `srcset` at all — so a failure falls all
    the way back to the plain `src` and the original file."""
    practitioner = with_headshot(b"this is not a jpeg at all")

    assert images.srcset(practitioner.headshot) == ""


def test_a_storage_outage_gives_an_empty_srcset(with_headshot, monkeypatch):
    practitioner = with_headshot(photo())

    def boom(*args, **kwargs):
        raise OSError("the bucket is unreachable")

    monkeypatch.setattr(storages["default"], "open", boom)

    assert images.srcset(practitioner.headshot) == ""


def test_a_missing_original_gives_an_empty_srcset():
    """A row whose file was deleted out from under it. The page still renders — with
    a broken `<img>`, which is the pre-existing behaviour, not a 500."""
    practitioner = PractitionerFactory(published=True, slug="ghost-photo")
    practitioner.headshot.name = "headshots/never-existed.jpg"
    practitioner.save(update_fields=["headshot"])

    assert images.srcset(practitioner.headshot) == ""


def test_the_logging_calls_are_actually_emittable(with_headshot, caplog):
    """Both log lines in this module, at a level that actually reaches ``makeRecord``.

    The reason this test exists rather than being obviously unnecessary: an ``extra``
    key that collides with a reserved ``LogRecord`` attribute makes
    ``Logger.makeRecord`` raise ``KeyError``, and both calls here originally used
    ``extra={"name": ...}`` — ``name`` is the logger's own name. So the "never fatal"
    promise raised instead, and the *whole home page* 500'd on any image failure.

    The suite could not see it. ``config/settings/test.py`` sets the root logger to
    CRITICAL, so ``isEnabledFor`` is False, ``_log`` is never called and
    ``makeRecord`` is never reached — the collision cannot happen in a test run.
    `caplog.set_level` is what makes it happen, so this is the one test in the module
    that runs with logging on.
    """
    practitioner = with_headshot(photo())
    storage = storages["default"]
    target = images.rendition_name(practitioner.headshot.name, 80)

    with caplog.at_level(logging.INFO, logger="directory.images"):
        # The failure path: `logger.exception(...)`.
        broken = PractitionerFactory(published=True, slug="broken-photo")
        broken.headshot.name = "headshots/gone.jpg"
        assert images.srcset(broken.headshot) == ""

        # The race path: `logger.info(...)`.
        storage.save(target, ContentFile(b"the other request got here first"))
        original_exists, lied = storage.exists, []

        def exists(name):
            if name == target and not lied:
                lied.append(name)
                return False
            return original_exists(name)

        storage.exists = exists
        try:
            assert images.srcset(practitioner.headshot) != ""
        finally:
            storage.exists = original_exists

    messages = [record.message for record in caplog.records]
    assert "images.srcset_failed" in messages
    assert "images.rendition_race" in messages


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------


def test_a_rendition_carries_no_exif(with_headshot):
    """A phone photo carries GPS coordinates. Re-encoding through Pillow without an
    `exif` argument writes none of it, so a practitioner who photographs themselves
    at home does not publish their home's coordinates in a file the whole internet
    can fetch (golden rule #4)."""
    buffer = io.BytesIO()
    source = Image.new("RGB", (600, 800), color=(10, 20, 30))
    exif = source.getexif()
    exif[0x0112] = 6  # Orientation
    exif[0x010E] = "taken at 51.336, -0.267"  # ImageDescription, standing in for GPS
    source.save(buffer, format="JPEG", exif=exif)

    practitioner = with_headshot(buffer.getvalue())
    images.srcset(practitioner.headshot)

    storage = storages["default"]
    with storage.open(images.rendition_name(practitioner.headshot.name, 80), "rb") as handle:
        assert dict(Image.open(handle).getexif()) == {}


def test_renditions_go_to_the_public_store_never_the_private_one(with_headshot):
    """Headshots are public; verification evidence is not, and the two backends are
    never the same store (directory/storages.py)."""
    practitioner = with_headshot(photo())
    images.srcset(practitioner.headshot)

    assert not getattr(storages["default"], "is_private_evidence", False)
    assert storages["default"].exists(images.rendition_name(practitioner.headshot.name, 80))
