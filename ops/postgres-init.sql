-- Extensions the app cannot create for itself: CREATE EXTENSION needs superuser,
-- and the application role deliberately is not one.
--
-- postgis  — GeoDjango PointField + the GiST index (docs/architecture.md).
-- pg_trgm  — trigram similarity for the search app (Phase 4). Created now so a
--            later migration does not need elevated rights.
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
