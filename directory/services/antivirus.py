"""Scanning an upload before it is stored.

Practitioners upload passport scans, DBS certificates and insurance schedules into
a bucket that Kiam staff then open. An infected file there is a route from an
unverified account to a verifier's laptop, so the scan is a control rather than a
nicety.

**It fails closed, and that is the whole design.** The default backend is
``reject``: with nothing configured, every upload is refused with a message that
says why. The alternative default — accept anything when no scanner is reachable —
is a control that switches itself off in exactly the circumstance it exists for, and
it does it silently. This mirrors ``directory.services.lint``, where a missing POM
dictionary raises rather than permitting everything, and for the same reason.

``ANTIVIRUS_BACKEND`` is therefore something a deployment must decide out loud:

* ``clamav`` — talk to a ClamAV daemon over TCP. Production.
* ``reject`` — refuse every upload. The default, and what an unconfigured
  production box does.
* ``skip`` — accept without scanning. Development only, named so it cannot be
  mistaken for anything else, logs a warning on every single call, and
  ``config/settings/prod.py`` must never select it —
  ``test_production_settings_do_not_skip_the_virus_scan`` is the guard.

**No new dependency.** ClamAV's INSTREAM command is a length-prefixed byte
protocol over a socket; a client for it is forty lines. Adding ``pyclamd`` to talk
to a daemon we already have to run would be a supply-chain edge for no gain.

**The scan reads the upload, not the stored file.** Django buffers an upload to a
temporary file or to memory; this runs against that, before
``documents.upload()`` hands anything to storage. There is no window in which an
unscanned object exists in the evidence bucket.

Size and type limits live here too, because "is this file acceptable" is one
question at one moment. They are not a substitute for the scan and the scan is not a
substitute for them: a 4GB file is a denial-of-service whether or not it is clean.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger("directory.antivirus")

#: What a practitioner may upload as evidence. A certificate is a PDF or a photo of
#: a document; nothing here needs to accept an archive or an Office file, and both
#: are the usual carriers.
ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/heif",
        "image/webp",
    }
)

ALLOWED_EXTENSIONS = frozenset({".pdf", ".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"})

#: 25 MB. A phone photo of a certificate is 2–8 MB; a scanned multi-page PDF can
#: reach 20. Anything larger is a mistake or an attack.
MAX_BYTES = 25 * 1024 * 1024

#: How much of the file to hand the scanner in one chunk.
CHUNK_BYTES = 64 * 1024


class UploadRejected(Exception):
    """The file may not be stored. The message is shown to the practitioner."""


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    backend: str
    #: The signature name when infected, or the reason when the scan could not run.
    detail: str = ""


def backend_name() -> str:
    return getattr(settings, "ANTIVIRUS_BACKEND", "reject")


def scan(upload) -> ScanResult:
    """Scan an ``UploadedFile``. Never raises for an infected file — returns a result.

    Raises only when the *scanner* cannot be trusted to have run, which the caller
    must treat exactly as it treats an infection.
    """
    name = backend_name()
    handler = _BACKENDS.get(name)

    if handler is None:
        # A typo in a settings value must not open the gate.
        logger.error("antivirus.unknown_backend", extra={"backend": name})
        return ScanResult(clean=False, backend=name, detail="No usable virus scanner is configured.")

    return handler(upload)


def check_acceptable(upload) -> None:
    """Size and type. Raises ``UploadRejected`` with a message for the practitioner.

    Run before the scan: there is no reason to stream 25MB at a daemon to find out
    it is a ZIP file.
    """
    import os

    size = getattr(upload, "size", None) or 0
    if size <= 0:
        raise UploadRejected("That file appears to be empty.")
    if size > MAX_BYTES:
        raise UploadRejected(
            f"That file is {size // (1024 * 1024)} MB. The limit is "
            f"{MAX_BYTES // (1024 * 1024)} MB — a photo or a scan of the document is plenty."
        )

    extension = os.path.splitext(getattr(upload, "name", "") or "")[1].lower()
    content_type = (getattr(upload, "content_type", "") or "").lower().split(";")[0].strip()

    # BOTH have to be acceptable. The browser-supplied content type is attacker
    # controlled, and so is the extension; requiring both agree with the allowlist
    # costs an honest user nothing and removes the easiest mismatch.
    if extension not in ALLOWED_EXTENSIONS or (content_type and content_type not in ALLOWED_MIME_TYPES):
        raise UploadRejected(
            "We can accept a PDF or a photo (JPG, PNG, HEIC or WebP). "
            "If you have a Word document or a ZIP file, please export it as a PDF first."
        )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _reject(upload) -> ScanResult:  # noqa: ARG001
    logger.error("antivirus.not_configured")
    return ScanResult(
        clean=False,
        backend="reject",
        detail=(
            "Uploads are turned off because no virus scanner is configured. "
            "Please email us and we will sort it out."
        ),
    )


def _skip(upload) -> ScanResult:  # noqa: ARG001
    """Development only. Loud on every call, deliberately."""
    logger.warning(
        "antivirus.skipped",
        extra={"upload": getattr(upload, "name", "")},
    )
    return ScanResult(clean=True, backend="skip", detail="not scanned")


def _clamav(upload) -> ScanResult:
    """ClamAV INSTREAM over TCP.

    The protocol: send ``zINSTREAM\\0``, then a series of ``<4-byte big-endian
    length><chunk>`` frames, then a zero-length frame, then read one line. A reply
    ending ``OK`` is clean; ``FOUND`` names the signature; anything else — including
    a socket that will not open — is a scan that did not happen.
    """
    host = getattr(settings, "CLAMAV_HOST", "127.0.0.1")
    port = int(getattr(settings, "CLAMAV_PORT", 3310))
    timeout = float(getattr(settings, "CLAMAV_TIMEOUT_SECONDS", 30))

    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"zINSTREAM\0")

            for chunk in _chunks(upload):
                sock.sendall(len(chunk).to_bytes(4, "big") + chunk)
            sock.sendall((0).to_bytes(4, "big"))

            reply = _read_reply(sock)
    except OSError:
        # DNS, refused, reset, timeout. The file is not known to be clean, so it is
        # treated as though it were not.
        logger.exception("antivirus.unreachable", extra={"host": host, "port": port})
        return ScanResult(
            clean=False,
            backend="clamav",
            detail="We could not run the virus scan just now. Please try again in a few minutes.",
        )
    finally:
        _rewind(upload)

    if reply.endswith("OK") and "FOUND" not in reply:
        return ScanResult(clean=True, backend="clamav")

    if "FOUND" in reply:
        signature = reply.split(":", 1)[-1].replace("FOUND", "").strip()
        logger.warning(
            "antivirus.infected",
            extra={"upload": getattr(upload, "name", ""), "signature": signature},
        )
        return ScanResult(clean=False, backend="clamav", detail=signature)

    logger.error("antivirus.unexpected_reply", extra={"reply": reply[:200]})
    return ScanResult(
        clean=False,
        backend="clamav",
        detail="The virus scan did not complete. Please try again in a few minutes.",
    )


_BACKENDS = {
    "reject": _reject,
    "skip": _skip,
    "clamav": _clamav,
}


def _chunks(upload):
    _rewind(upload)
    yield from upload.chunks(CHUNK_BYTES)


def _rewind(upload) -> None:
    """Put the file pointer back, or the caller stores zero bytes.

    Not defensive coding — this has to happen. ``chunks()`` leaves the handle at
    EOF, and ``Document.file.save()`` reads from wherever it is left.
    """
    seek = getattr(upload, "seek", None)
    if callable(seek):
        try:
            seek(0)
        except (OSError, ValueError):  # pragma: no cover — a closed handle
            logger.warning("antivirus.rewind_failed")


def _read_reply(sock) -> str:
    buffer = b""
    while b"\0" not in buffer and len(buffer) < 4096:
        received = sock.recv(4096)
        if not received:
            break
        buffer += received
    return buffer.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
