#!/usr/bin/env bash
# Thin wrapper for bash users: runs the same pipeline as `python run.py`.
# Picks the first Python 3 that actually works (on Windows, `python3` may be a Store placeholder).
cd "$(dirname "$0")" || exit 1
for py in python3 python py; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c "import sys; sys.exit(sys.version_info < (3, 9))" 2>/dev/null; then
    exec "$py" run.py "$@"
  fi
done
echo "Python 3.9 or newer is required." >&2
exit 1
