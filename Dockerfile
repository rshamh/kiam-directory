# syntax=docker/dockerfile:1.7
#
# Three stages, for two reasons that both come down to secrets and size:
#
# 1. `kiam-ui` installs over SSH from a private repo. The key is mounted with
#    `--mount=type=ssh`, so it is available to pip and never enters a layer.
#    Build with:  docker build --ssh default .
#
# 2. The asset stage is node-only and has no Python, so it cannot run
#    `manage.py kiam_ui_vendor`. It COPYs the same two directories out of the
#    Python stage instead — exactly what kiam-ui's README prescribes.
#
# GDAL and GEOS are runtime requirements of GeoDjango, not build-time ones, so
# they are installed in the final stage.

# ---------------------------------------------------------------------------
# 1. Python dependencies
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS pydeps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git openssh-client libgdal-dev libgeos-dev \
    && rm -rf /var/lib/apt/lists/*

# Pre-seed the host key so the SSH fetch of kiam-ui is not an interactive prompt.
RUN mkdir -p -m 0700 /root/.ssh && ssh-keyscan github.com >> /root/.ssh/known_hosts

WORKDIR /app
COPY pyproject.toml README.md ./

# The forwarded agent is what makes the private git+ssh dependency resolvable.
RUN --mount=type=ssh pip install --prefix=/install .

# ---------------------------------------------------------------------------
# 2. Front-end assets
# ---------------------------------------------------------------------------
FROM node:20-slim AS assets

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --omit=optional || npm install

COPY static/ ./static/
COPY templates/ ./templates/

# The vendor step, done by COPY because this stage has no Python. Same two
# directories `manage.py kiam_ui_vendor` would produce, at the same path the
# Tailwind entry's @source and @import expect.
COPY --from=pydeps /install/lib/python3.13/site-packages/kiam_ui/static/kiam_ui/css/src ./build/kiam-ui/css
COPY --from=pydeps /install/lib/python3.13/site-packages/kiam_ui/templates          ./build/kiam-ui/templates

RUN npx tailwindcss -i ./static/src/app.css -o ./static/css/app.css --minify

# ---------------------------------------------------------------------------
# 3. Runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# GDAL and GEOS are what GeoDjango loads through ctypes at runtime. The Debian
# packages land on the default linker path, so no GDAL_LIBRARY_PATH is needed
# here — unlike on macOS/Homebrew. See README.md.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin libgdal36 libgeos-c1v5 libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Never run as root.
RUN useradd --create-home --shell /usr/sbin/nologin app
WORKDIR /app

COPY --from=pydeps /install /usr/local
COPY --chown=app:app . .
COPY --from=assets --chown=app:app /app/static/css ./static/css

# collectstatic needs a SECRET_KEY and a settings module that imports; it does
# not need a database. A throwaway key here keeps the real one out of the image.
RUN SECRET_KEY=build-only ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput --settings=config.settings.base

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
