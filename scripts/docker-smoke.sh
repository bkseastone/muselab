#!/usr/bin/env bash
# Exercise startup, authenticated configuration and recreation with private fake state.
set -euo pipefail
SMOKE_IMAGE="${1:-muselab:ci}"
SMOKE_NAME="muselab-smoke-${BASHPID}-${RANDOM}"
SMOKE_VOLUME="${SMOKE_NAME}-sessions"
SMOKE_STATE="$(mktemp -d)"
SMOKE_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod 700 "$SMOKE_STATE"
cleanup() {
  docker rm -f "$SMOKE_NAME" >/dev/null 2>&1 || true
  docker volume rm "$SMOKE_VOLUME" >/dev/null 2>&1 || true
  rm -rf "$SMOKE_STATE"
}
trap cleanup EXIT
printf 'MUSELAB_TOKEN=container-smoke-fixture-token-not-a-real-secret\nMUSELAB_ROOT=/data\nMUSELAB_BUSY_SEND_MODE=adjust\n' > "$SMOKE_STATE/env"
chmod 600 "$SMOKE_STATE/env"
docker volume create "$SMOKE_VOLUME" >/dev/null
boot() {
  docker run -d --name "$SMOKE_NAME" --env-file "$SMOKE_STATE/env" \
    --mount "type=volume,source=$SMOKE_VOLUME,target=/app/sessions" "$SMOKE_IMAGE" >/dev/null
  for ((attempt=0; attempt<60; attempt++)); do
    if docker exec "$SMOKE_NAME" curl -fsS --max-time 2 http://127.0.0.1:8765/api/health >/dev/null 2>&1; then
      docker exec "$SMOKE_NAME" curl -fsS --max-time 5 http://127.0.0.1:8765/ >/dev/null
      docker exec "$SMOKE_NAME" curl -fsS --max-time 5 http://127.0.0.1:8765/static/app.js >/dev/null
      return
    fi
    [[ "$(docker inspect --format '{{.State.Running}}' "$SMOKE_NAME")" == true ]] || break
    sleep 1
  done
  printf 'Container did not become ready.\n' >&2
  return 1
}
boot
docker exec -i "$SMOKE_NAME" python - seed < "$SMOKE_SCRIPT_DIR/docker-state-smoke.py"
# A new container must read the same volume; restart alone cannot prove this.
docker stop --time 20 "$SMOKE_NAME" >/dev/null
docker rm "$SMOKE_NAME" >/dev/null
boot
docker exec -i "$SMOKE_NAME" python - verify < "$SMOKE_SCRIPT_DIR/docker-state-smoke.py"
for ((probe=0; probe<45; probe++)); do
  SMOKE_HEALTH="$(docker inspect --format '{{.State.Health.Status}}' "$SMOKE_NAME")"
  if [[ "$SMOKE_HEALTH" == healthy ]]; then
    printf 'Container auth, config, transcript recreation, assets and HEALTHCHECK passed.\n'
    exit 0
  fi
  [[ "$SMOKE_HEALTH" != unhealthy ]] || break
  sleep 2
done
printf 'Container HEALTHCHECK did not reach healthy.\n' >&2
exit 1
