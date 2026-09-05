#!/usr/bin/env bash
# Validate the project's frozen SDK + bundled CLI in a candidate environment,
# then promote it. The active environment survives installation/test failures.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"
if [[ "${1:-}" == "--help" ]]; then
  printf 'Usage: bash scripts/upgrade.sh [--check]\nValidates frozen dependencies; --check keeps the current environment.\n'
  exit 0
fi
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--check" ) ]]; then
  printf 'Unknown option. Use --help.\n' >&2; exit 2
fi
command -v uv >/dev/null || { printf 'Install uv before upgrading.\n' >&2; exit 1; }
umask 077
ENV_STORE="$PROJECT_ROOT/.venv-builds"
mkdir -p "$ENV_STORE"
LOCK_DIR="$ENV_STORE/.upgrade-lock"
mkdir "$LOCK_DIR" 2>/dev/null || {
  printf 'Another upgrade is running, or an interrupted run left %s. Verify the prior process before removing that lock.\n' "$LOCK_DIR" >&2
  exit 1
}
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
CANDIDATE="$(mktemp -d "$ENV_STORE/candidate.XXXXXX")"
printf 'Installing the versions approved by pyproject.toml and uv.lock.\n'
if ! UV_PROJECT_ENVIRONMENT="$CANDIDATE" uv sync --frozen >"$CANDIDATE/install.log" 2>&1; then
  printf 'Dependency installation failed; current .venv is unchanged. Private log: %s/install.log\n' "$CANDIDATE" >&2
  exit 1
fi
"$CANDIDATE/bin/python" - <<'PY'
from claude_agent_sdk._version import __version__
from claude_agent_sdk._cli_version import __cli_version__
print(f'Candidate SDK: {__version__}; bundled Claude CLI: {__cli_version__}')
PY
TEST_ROOT="$CANDIDATE/test-workspace"
mkdir -p "$TEST_ROOT"
printf 'Running isolated unit/integration checks.\n'
if ! MUSELAB_ROOT="$TEST_ROOT" MUSELAB_SESSIONS_DIR="$TEST_ROOT/sessions" \
  MUSELAB_ENV_PATH="$TEST_ROOT/test.env" \
  MUSELAB_TOKEN='upgrade-isolated-fixture-token-not-a-real-secret' RUN_E2E=0 \
  "$CANDIDATE/bin/python" -m pytest tests/ -q \
  >"$CANDIDATE/tests.log" 2>&1; then
  printf 'Tests failed; current .venv is unchanged. Private log: %s/tests.log\n' "$CANDIDATE" >&2
  exit 1
fi
tail -2 "$CANDIDATE/tests.log"
if [[ "${1:-}" == "--check" ]]; then
  printf 'Validation passed. Current .venv is unchanged. Candidate: %s\n' "$CANDIDATE"
  exit 0
fi
# Keep candidates at their original paths: venv entrypoint shebangs are absolute.
# A retained previous link/directory makes a manual rollback recoverable.
"$CANDIDATE/bin/python" - "$PROJECT_ROOT" "$CANDIDATE" <<'PY'
import os
import sys
from pathlib import Path
root, candidate = map(Path, sys.argv[1:])
current = root / '.venv'
backup = root / '.venv-builds' / ('previous.' + candidate.name)
next_link = root / ('.venv.next.' + candidate.name)
next_link.symlink_to(candidate, target_is_directory=True)
moved = False
try:
    if current.is_symlink():
        # Resolve a relative original link before placing its backup elsewhere.
        backup.symlink_to(current.resolve(), target_is_directory=True)
        os.replace(next_link, current)
    else:
        if current.exists():
            os.rename(current, backup)
            moved = True
        try:
            os.replace(next_link, current)
        except BaseException:
            if moved:
                os.rename(backup, current)
            raise
finally:
    if next_link.is_symlink():
        next_link.unlink()
print(f'Activated validated environment: {candidate}')
if backup.exists() or backup.is_symlink():
    print(f'Previous environment retained: {backup}')
PY
printf 'Upgrade complete. Restart the exact application service when its tasks are idle.\n'
printf 'The standalone system Claude CLI, Git files, .env and workspace data were not changed.\n'
