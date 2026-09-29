#!/usr/bin/env bash
# Run the complete application inside a GitHub Codespace (free monthly quota) and print its URL.
# Usage: bash deploy/codespace.sh [up|down|reset|logs|token]. Secrets live in .local/codespace/
# (0600, git-ignored) and survive codespace restarts; data lives in Docker volumes.
# CRP_PROFILE=lite runs the Render free configuration (one process, 512 MB, 0.1 CPU).
set -euo pipefail
cd "$(dirname "$0")/.."

command="${1:-up}"
compose=(docker compose -f deploy/docker-compose.yml)
if [ "${CRP_PROFILE:-standard}" = "lite" ]; then
  compose+=(-f deploy/docker-compose.lite.yml)
fi
state=.local/codespace
mkdir -p "$state"
chmod 700 "$state"
for secret in access-token db-password; do
  if [ ! -s "$state/$secret" ]; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > "$state/$secret"
  fi
  chmod 600 "$state/$secret"
done
export CRP_ACCESS_TOKEN CRP_DB_PASSWORD CRP_PUBLIC_HOST
CRP_ACCESS_TOKEN="$(cat "$state/access-token")"
CRP_DB_PASSWORD="$(cat "$state/db-password")"
if [ -n "${CODESPACE_NAME:-}" ] && [ -n "${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-}" ]; then
  CRP_PUBLIC_HOST="${CODESPACE_NAME}-8080.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN}"
elif [ -z "${CRP_PUBLIC_HOST:-}" ]; then
  echo "Not in a GitHub Codespace: set CRP_PUBLIC_HOST to the https host name that serves port 8080." >&2
  exit 2
fi

case "$command" in
  up)
    build_flag=--build
    if [ "${CRP_SKIP_BUILD:-0}" = "1" ]; then build_flag=--no-build; fi
    echo "Starting the application (the first build takes several minutes)..."
    "${compose[@]}" up -d "$build_flag"
    for _ in $(seq 1 "${CRP_START_TIMEOUT_TICKS:-300}"); do
      if curl -fsS http://127.0.0.1:8080/v1/health/live > /dev/null 2>&1; then
        echo
        echo "refactorX is running:  https://${CRP_PUBLIC_HOST}"
        echo "Sign-in token:         bash deploy/codespace.sh token (or use Try the demo)"
        exit 0
      fi
      sleep 2
    done
    echo "The application did not become live; see: bash deploy/codespace.sh logs" >&2
    exit 1
    ;;
  down) "${compose[@]}" down ;;
  reset) "${compose[@]}" down --volumes ;; # stop and delete all projects, scans and the database
  logs) "${compose[@]}" logs --tail 200 app ;;
  token) cat "$state/access-token" ;;
  *) echo "usage: bash deploy/codespace.sh [up|down|reset|logs|token]" >&2; exit 2 ;;
esac
