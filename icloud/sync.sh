#!/usr/bin/env bash
# Hourly download of every iCloud library, then one Immich scan.
# An expired Apple session removes the health file and stays running so
# `docker compose ps` shows this container unhealthy instead of crash-looping.

set -euo pipefail

STATE=/tmp/sync-state
INTERVAL="${ICLOUD_SYNC_INTERVAL:-3600}"
FAILURES=0
export ICLOUD_COOKIE_LOCATION="${ICLOUD_COOKIE_LOCATION:-/cookies}"

mkdir -p "$STATE"

stay_unhealthy() {
  rm -f "$STATE/healthy" "$STATE/downloading"
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

run_icloudpd() {
  set +e
  /app/icloudpd "${ICLOUDPD_ARGS[@]}" >/tmp/icloudpd.out 2>&1
  local code=$?
  set -e
  cat /tmp/icloudpd.out
  return "$code"
}

while true; do
  touch "$STATE/downloading"
  rm -f "$STATE/needs-reauth"
  icloudpd_args list "$ICLOUD_USERNAME"
  if ! run_icloudpd; then
    if python3 /usr/local/bin/icloud_library.py --session-expired /tmp/icloudpd.out 1; then
      python3 /usr/local/bin/icloud_library.py --reauth-message >&2
      stay_unhealthy
    fi
    FAILURES=$((FAILURES + 1))
    echo "Could not list iCloud libraries, attempt ${FAILURES}." >&2
    if [[ "$FAILURES" -ge 5 ]]; then
      echo "Listing iCloud libraries failed 5 times. The container is now unhealthy." >&2
      echo "If Apple rejected the session, run: ./import-icloud.sh ${ICLOUD_USERNAME}" >&2
      stay_unhealthy
    fi
    rm -f "$STATE/downloading"
    sleep 300
    continue
  fi

  mapfile -t libraries < <(icloud_parse_libraries < /tmp/icloudpd.out | icloud_sort_libraries)
  if [[ ${#libraries[@]} -eq 0 ]]; then
    echo "No PrimarySync or SharedSync library in the list. Using PrimarySync." >&2
    libraries=(PrimarySync)
  fi
  echo "Libraries: ${libraries[*]}"
  migrate_flat_download_into_primary /data

  cycle_failed=0
  for library in "${libraries[@]}"; do
    icloudpd_args sync "$ICLOUD_USERNAME" "$library"
    if [[ "$ICLOUDPD_INCREMENTAL" -eq 1 ]]; then
      echo "Incremental download of ${library}."
    else
      echo "Full download of ${library}. This pass does not stop early."
    fi
    touch "$STATE/downloading"
    if ! run_icloudpd; then
      if python3 /usr/local/bin/icloud_library.py --session-expired /tmp/icloudpd.out "1"; then
        python3 /usr/local/bin/icloud_library.py --reauth-message >&2
        stay_unhealthy
      fi
      echo "Download of ${library} failed." >&2
      cycle_failed=1
      continue
    fi
    if [[ "$ICLOUDPD_INCREMENTAL" -eq 0 ]]; then
      mark_icloud_full_pass "$library"
      echo "Recorded a completed full pass for ${library}."
    fi
  done

  rm -f "$STATE/downloading"
  if [[ "$cycle_failed" -ne 0 ]]; then
    FAILURES=$((FAILURES + 1))
    echo "iCloud download failed, attempt ${FAILURES}." >&2
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
  echo "Immich scan queued. Next iCloud check in ${INTERVAL}s."
  sleep "$INTERVAL"
done
