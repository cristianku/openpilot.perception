#!/usr/bin/env bash
# Start perception-web by hand, without the systemd service (install.sh installs the service):
#
#   web/run.sh [--host 0.0.0.0] [--port 8767]
#
# PERCEPTION_WEB_TOKEN, the setup code of the first visit, comes from the .env of this clone when it is
# there (install.sh writes it); otherwise the site prints a new one when it starts.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
[ -x "$ROOT/.venv/bin/python" ] || { echo "No environment: run sudo ./install.sh first (or uv sync)." >&2; exit 1; }
if [ -z "${PERCEPTION_WEB_TOKEN:-}" ] && [ -f "$ROOT/.env" ]; then
  token="$(sed -n 's/^PERCEPTION_WEB_TOKEN=//p' "$ROOT/.env" | tail -1)"
  [ -n "$token" ] && export PERCEPTION_WEB_TOKEN="$token"
fi
cd "$ROOT"
exec "$ROOT/.venv/bin/python" -m web.server "$@"
