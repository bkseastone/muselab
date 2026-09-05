#!/usr/bin/env bash
# Exercise the built image with disposable state and no provider credentials.
set -euo pipefail
SMOKE_IMAGE="${1:-muselab:ci}"
SMOKE_NAME="muselab-smoke-${BASHPID}-${RANDOM}"
SMOKE_STATE="$(mktemp -d)"
chmod 700 "$SMOKE_STATE"
cleanup() {
  docker rm -f "$SMOKE_NAME" >/dev/null 2>&1 || true
  rm -rf "$SMOKE_STATE"
}
trap cleanup EXIT
printf 'MUSELAB_TOKEN=container-smoke-fixture-token-not-a-real-secret\nMUSELAB_ROOT=/data\n' > "$SMOKE_STATE/env"
chmod 600 "$SMOKE_STATE/env"
docker run -d --name "$SMOKE_NAME" --env-file "$SMOKE_STATE/env" "$SMOKE_IMAGE" >/dev/null
for ((attempt=0; attempt<60; attempt++)); do
  if docker exec "$SMOKE_NAME" curl -fsS --max-time 2 \
      http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
    docker exec "$SMOKE_NAME" curl -fsS --max-time 5 http://127.0.0.1:8765/ >/dev/null
    docker exec "$SMOKE_NAME" curl -fsS --max-time 5 http://127.0.0.1:8765/static/app.js >/dev/null
    for ((probe=0; probe<45; probe++)); do
      SMOKE_HEALTH="$(docker inspect --format '{{.State.Health.Status}}' "$SMOKE_NAME")"
      if [[ "$SMOKE_HEALTH" == healthy ]]; then
        printf 'Container health, homepage and frontend assets passed.\n'; exit 0
      fi
      [[ "$SMOKE_HEALTH" != unhealthy ]] || break
      sleep 2
    done
    printf 'Container HEALTHCHECK did not reach healthy.\n' >&2; exit 1
  fi
  [[ "$(docker inspect --format '{{.State.Running}}' "$SMOKE_NAME")" == true ]] || break
  sleep 1
done
printf 'Container did not become ready.\n' >&2
exit 1
