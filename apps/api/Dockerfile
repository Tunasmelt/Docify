# Docify API — Docker-only on Render (ARCHITECTURE.md's hard deploy
# constraint: Render's native Python runtime blocks apt-get/OS-level
# installs entirely, and both Tesseract (FEAT-017 tier-3 OCR fallback —
# a system binary, not a Python package) and Docling's own image
# processing need real system libraries a container can install).

FROM python:3.12-slim

# tesseract-ocr: FEAT-017 tier-3 OCR fallback's actual binary — pytesseract
#   (pyproject.toml) is only a thin wrapper that shells out to this.
# libgl1 / libglib2.0-0: Docling's dependency tree pulls in OpenCV-backed
#   image processing at import time (even with do_ocr=False, which this
#   project always sets — MEMORY.md's Docling-defaults entry — the import
#   itself still needs libGL.so.1 on a minimal Debian base; a documented,
#   common Docling-in-Docker requirement, included defensively rather than
#   discovered via a failed deploy).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# uv, pinned to match this project's lockfile format — copied as a static
# binary from its own official image rather than `pip install uv`, so the
# build doesn't need a working pip resolve just to bootstrap the real
# resolver.
COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

WORKDIR /app

# Dependencies installed from the lockfile BEFORE the rest of the source
# is copied in, so an app-code-only change doesn't invalidate Docker's
# dependency-install layer cache. --frozen: fail the build rather than
# silently re-resolve if pyproject.toml and uv.lock have drifted —
# exactly the guarantee a reproducible deploy needs. --no-dev: pytest/
# psycopg[binary]/etc. (dependency-groups.dev) are test-only, never
# imported by the running app (main.py/routes/services) — excluding them
# keeps the deployed image smaller and its dependency surface smaller.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Real app source — deliberately not `COPY . .` with a blanket .dockerignore
# alone trusted silently; the explicit list below is what actually needs
# to exist at runtime (main.py's imports, migrations/ for reference only —
# not applied by the running app, kept out on purpose, see below).
COPY main.py errors.py ./
COPY db ./db
COPY middleware ./middleware
COPY models ./models
COPY routes ./routes
COPY services ./services

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Render injects the real listen port via $PORT at runtime — shell form
# (not exec-form JSON array) is required here specifically so that
# expansion actually happens; an exec-form CMD would pass the literal
# string "$PORT" through unexpanded. Defaults to 8000 for any non-Render
# environment (e.g. `docker run` locally) where $PORT is unset.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
