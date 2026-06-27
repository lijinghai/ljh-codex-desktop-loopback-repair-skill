#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=python3
elif command -v python >/dev/null 2>&1 && python -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' >/dev/null 2>&1; then
  PYTHON_BIN=python
else
  echo "python3 is required to start the repair web panel on Ubuntu." >&2
  exit 1
fi

exec "$PYTHON_BIN" scripts/start_repair_web.py "$@"
