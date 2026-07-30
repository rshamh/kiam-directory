"""Delete evidence whose retention period has elapsed.

``publication.request_removal()`` sets ``Document.delete_after`` when somebody
asks to be removed from the directory, and ``templates/dashboard/remove.html``
tells them "after that they are deleted automatically". Until Phase 6's compliance
review, nothing acted on the column and that sentence was false — the data subject
was being told their documents would go, and they would have sat in the evidence
bucket indefinitely. A retention promise nobody keeps is worse than no promise,
because it is the one they relied on.

Three things about this command are deliberate.

**It deletes the file AND the row.** A ``Document`` row left behind with its file
gone still carries the original filename and a SHA-256 of the contents, which is
metadata about a document somebody asked to have erased.

**The audit entry survives the document.** ``AuditLog`` keeps a record that a
deletion happened, with the document's id and type, and no filename or hash. That
is what lets Kiam answer "did you honour the request" without keeping the thing it
was asked to destroy.

**It is idempotent and never partial.** Each document is its own transaction, so a
storage failure on one file does not roll back the deletions that already
succeeded, and a re-run picks up whatever is left.

Scheduled in ``ops/crontab`` at 04:00 — after ``verification_sweep`` (03:00) and
``rebuild_search_index`` (03:30), because a document deleted mid-sweep would make
the sweep's own reasoning about evidence wrong.
"""

from __future__ import annotations

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from directory.models import AuditLog, Document

logger = logging.getLogger("directory.retention")


class Command(BaseCommand):
    help = "Delete verification evidence whose delete_after date has passed."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be deleted and delete nothing.",
        )

    def handle(self, *args, **options):
        now = timezone.now()
        due = Document.objects.filter(delete_after__isnull=False, delete_after__lte=now).select_related(
            "practitioner"
        )

        if options["dry_run"]:
            for document in due:
                self.stdout.write(
                    f"would delete {document.pk} ({document.type}) "
                    f"for {document.practitioner_id}, due {document.delete_after:%Y-%m-%d}"
                )
            self.stdout.write(self.style.WARNING(f"{due.count()} document(s) due. Nothing deleted."))
            return

        deleted = failed = 0
        for document in due:
            try:
                with transaction.atomic():
                    # The audit entry is written BEFORE the file goes, and in the
                    # same transaction, so there is no ordering in which evidence
                    # is destroyed and the record of destroying it is not — the
                    # same rule `documents.open_evidence()` follows for reads.
                    AuditLog.objects.create(
                        actor=None,
                        action="evidence.retention_deleted",
                        entity_type="Document",
                        entity_id=str(document.pk),
                        before={
                            "practitioner_id": str(document.practitioner_id),
                            "type": document.type,
                            "uploaded_at": document.uploaded_at.isoformat(),
                            "delete_after": document.delete_after.isoformat(),
                        },
                    )
                    document.file.delete(save=False)
                    document.delete()
                deleted += 1
            except Exception:  # noqa: BLE001 — one bad file must not stop the rest
                failed += 1
                logger.exception("retention.delete_failed", extra={"document_id": str(document.pk)})

        logger.info("retention.swept", extra={"deleted": deleted, "failed": failed})
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} document(s). {failed} failed."))
