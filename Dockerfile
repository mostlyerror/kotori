# Single-stage Python build — kotorid is a foreground async process with
# no compiled extensions, so a slim base + pip install is enough. The TUI
# (kotori_tui) is included but isn't entered as the container's command;
# this container is meant to run kotorid only.

FROM python:3.13-slim

WORKDIR /app

# Install just what we need so the image stays small. schema.sql ships
# inside kotorid/ now, so no separate db/ directory to copy.
COPY pyproject.toml ./
COPY kotorid ./kotorid
COPY kotori_tui ./kotori_tui

# Non-editable install bakes the package into site-packages and registers
# the `kotorid` and `kotori` console scripts. We invoke the module form
# below rather than relying on the script shebang (more robust against
# PATH shifts and signal handling).
RUN pip install --no-cache-dir .

# Default to a /data-mounted volume for the persistent SQLite file.
# Railway (and most PaaS) mount the persistent volume at /data; override
# via the KOTORI_DB env var if your host uses a different mount point.
ENV KOTORI_DB=/data/kotori.db

# Stream logs unbuffered so Railway's log viewer sees output immediately
# rather than waiting for Python's default buffering to flush.
ENV PYTHONUNBUFFERED=1

# kotorid handles SIGTERM/SIGINT for graceful shutdown (see __main__.run).
# Container orchestrators send SIGTERM on stop, which trips our shutdown
# path before the 30s default kill timeout.
CMD ["python", "-m", "kotorid"]
