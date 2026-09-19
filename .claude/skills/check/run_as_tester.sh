#!/bin/sh
# Run the test suite as an unprivileged user, so a permission test means
# something. Root reads any file, so six tests that make a path unreadable
# pass for the wrong reason under root.
#
# Usage: bash .claude/skills/check/run_as_tester.sh [pytest arguments]
set -eu

cd "$(git rev-parse --show-toplevel)"

if [ "$(id -u)" != "0" ]; then
  exec uv run poe test "$@"
fi

user=tester
if ! id "$user" >/dev/null 2>&1; then
  useradd -m -s /bin/sh "$user"
fi

home=$(mktemp -d /tmp/"$user".XXXXXX)
chown "$user" "$home"

# The virtual environment is readable by everyone by default. The suite runs
# from it directly, so no network and no package manager is needed.
runuser -u "$user" -- env HOME="$home" COVERAGE_FILE="$home/.coverage" \
  .venv/bin/python -m pytest -q -o cache_dir="$home/pytest" -p no:cacheprovider "$@"
