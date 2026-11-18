#!/bin/sh
# M&Q AI Command Dashboard — local foundation server.
# Binds to 127.0.0.1 only. This is a local build, not a deployment.
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-$HOME/.venvs/cmd-dash/bin/python}"
exec "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT:-8000}"
