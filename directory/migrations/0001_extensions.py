"""PostGIS and pg_trgm.

Separated from the model migration, and deliberately first.

``CREATE EXTENSION`` needs superuser (or the ``rds_superuser``-equivalent), and
the application role is not one. On a managed database the extensions are
normally created once by an operator; ``CreateExtension`` is idempotent — it
emits ``CREATE EXTENSION IF NOT EXISTS`` — so this migration is a no-op there
rather than a failure.

Keeping it as its own migration means the model migration that follows can be
replayed, squashed or reordered without dragging a privileged operation along
with it, and a restricted-role deployment can fake just this one:

    python manage.py migrate directory 0001_extensions --fake

postgis  — GeoDjango's PointField, the GiST index on PractitionerLocation.geo,
           and distance queries (docs/architecture.md, "Search").
pg_trgm  — trigram similarity for fuzzy name matching in Phase 4. Created now
           because it is the same privileged operation and doing it once is
           better than a second superuser step later.
"""

from django.contrib.postgres.operations import CreateExtension, TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        CreateExtension("postgis"),
        TrigramExtension(),
    ]
