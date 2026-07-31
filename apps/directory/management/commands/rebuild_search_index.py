"""Rebuild every practitioner's search vector.

The safety net behind the signals in ``directory/signals.py``. Signals cover the
edit paths, but not everything goes through them:

* a data migration or a ``bulk_create`` bypasses ``post_save`` entirely;
* ``queryset.update()`` emits no signal at all;
* a synonym added to ``taxonomy.py`` and loaded by ``seed_taxonomy`` changes the
  Speciality row, not the Practitioner — so every listing carrying that
  speciality has a stale vector and nothing has signalled about it.

That last one is the common case, which is why this runs nightly rather than
being kept for emergencies.
"""

from django.core.management.base import BaseCommand

from apps.directory.models import Practitioner, PublicationStatus
from apps.directory.services import search_index


class Command(BaseCommand):
    help = "Rebuild the full-text search vector for every practitioner."

    def add_arguments(self, parser):
        parser.add_argument(
            "--published-only",
            action="store_true",
            help="Only rebuild published listings (the ones search can return).",
        )

    def handle(self, *args, **options):
        queryset = Practitioner.objects.all()
        if options["published_only"]:
            queryset = queryset.filter(status=PublicationStatus.PUBLISHED)

        count = search_index.rebuild_all(queryset)
        self.stdout.write(self.style.SUCCESS(f"Rebuilt {count} search vectors."))
