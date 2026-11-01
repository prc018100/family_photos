#!/usr/bin/env bash
# Hourly iCloud download, then an Immich library scan.
# An expired Apple session removes the health file and stays running so
# `docker compose ps` shows this container unhealthy instead of crash-looping.

set -euo pipefail

STATE=/tmp/sync-state
INTERVAL="${ICLOUD_SYNC_INTERVAL:-3600}"
FAILURES=0

mkdir -p "$STATE"

stay_unhealthy() {
  rm -f "$STATE/healthy"
  touch "$STATE/needs-reauth"
  while true; do
    sleep 3600
  done
}

if [[ -z "${ICLOUD_USERNAME:-}" ]]; then
  echo "ICLOUD_USERNAME is empty. On the server run: ./import-icloud.sh you@icloud.com" >&2
  stay_unhealthy
fi

if [[ -z "${IMMICH_API_KEY:-}" ]]; then
  echo "IMMICH_API_KEY is empty. Set it in .env, then run ./import-icloud.sh ${ICLOUD_USERNAME}" >&2
  stay_unhealthy
fi

source /usr/local/bin/lib.sh

while true; do
  icloudpd_args sync "$ICLOUD_USERNAME"
  set +e
  /app/icloudpd "${ICLOUDPD_ARGS[@]}" >/tmp/icloudpd.out 2>&1
  code=$?
  set -e
  cat /tmp/icloudpd.out
  if [[ "$code" -ne 0 ]]; then
    if python3 /usr/local/bin/icloud_library.py --session-expired /tmp/icloudpd.out "$code"; then
      python3 /usr/local/bin/icloud_library.py --reauth-message >&2
      stay_unhealthy
    fi
    FAILURES=$((FAILURES + 1))
    echo "iCloud download failed (exit ${code}), attempt ${FAILURES}." >&2
    if [[ "$FAILURES" -ge 5 ]]; then
      echo "iCloud download failed 5 times in a row. The container is now unhealthy." >&2
      echo "If Apple rejected the session, run: ./import-icloud.sh ${ICLOUD_USERNAME}" >&2
      stay_unhealthy
    fi
    sleep 300
    continue
  fi

  FAILURES=0
  python3 /usr/local/bin/icloud_library.py
  touch "$STATE/healthy"
  rm -f "$STATE/needs-reauth"
  echo "Immich scan queued. Next iCloud check in ${INTERVAL}s."
  sleep "$INTERVAL"
done
