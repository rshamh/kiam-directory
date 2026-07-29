"""Nightly verification sweep. Cron/Celery beat at 03:00.

Calls ``verification.nightly_sweep()``, which expires stale checks, recomputes
every affected practitioner, and returns the notifications to send. This command
is the part that actually sends them and records what it sent.

Two things it is careful about:

**A provisional lapse is never silent.** When a DBS window runs out, the
practitioner's under-18 groups vanish from their public profile and from search.
They get an email and it goes in the admin queue (``verification-policy.md``).
A sweep that quietly dropped that notification would leave someone wondering why
their enquiries stopped.

**One bad address does not stop the sweep.** Sending is per-notification and
failures are logged and counted, not raised — a full mailbox on one account must
not prevent the other ninety-nine people being told their insurance expires
tomorrow.

    0 3 * * *  cd /app && python manage.py verification_sweep
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string

from directory.services import verification

logger = logging.getLogger("directory.verification")

SUBJECTS = {
    "provisional_lapsed": "Your under-18 listing has been paused",
    "expiry_reminder": "Your Kiam Clinic Directory verification is due for renewal",
}


class Command(BaseCommand):
    help = "Expire stale verification checks, recompute badges, and send reminders."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Recompute and report, but send no email.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        notifications = verification.nightly_sweep()

        sent = failed = 0
        for notification in notifications:
            kind = notification["kind"]
            practitioner = notification["practitioner"]

            logger.info(
                "verification.notification",
                extra={
                    "kind": kind,
                    "practitioner_id": str(practitioner.pk),
                    "days_left": notification.get("days_left"),
                    "dry_run": dry_run,
                },
            )

            if dry_run:
                continue

            try:
                self._send(notification)
            except Exception:
                # Logged, counted, and moved past — see the module docstring.
                failed += 1
                logger.exception(
                    "verification.notification_failed",
                    extra={"kind": kind, "practitioner_id": str(practitioner.pk)},
                )
            else:
                sent += 1

        summary = f"{len(notifications)} notification(s): {sent} sent, {failed} failed"
        if dry_run:
            summary = f"{len(notifications)} notification(s) — dry run, nothing sent"

        logger.info(
            "verification.sweep_complete",
            extra={"notifications": len(notifications), "sent": sent, "failed": failed},
        )
        self.stdout.write(self.style.SUCCESS(summary))
        return None

    def _send(self, notification) -> None:
        practitioner = notification["practitioner"]
        kind = notification["kind"]

        recipient = self._recipient(practitioner)
        if not recipient:
            # No contactable address. Still logged above, and the admin queue
            # entry written by nightly_sweep() is what surfaces it.
            logger.warning(
                "verification.notification_no_recipient",
                extra={"kind": kind, "practitioner_id": str(practitioner.pk)},
            )
            return

        body = render_to_string(
            f"directory/email/{kind}.txt",
            {
                "practitioner": practitioner,
                "days_left": notification.get("days_left"),
                "site_name": settings.KIAM_UI["SITE_NAME"],
                "site_url": settings.SITE_BASE_URL,
            },
        )
        send_mail(
            subject=SUBJECTS[kind],
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient],
            fail_silently=False,
        )

    @staticmethod
    def _recipient(practitioner) -> str:
        """The account email, not the published one.

        `public_email` is a business address a receptionist may read. Anything
        about verification state goes to the person who owns the account.
        """
        user = getattr(practitioner, "user", None)
        return user.email if user else ""
