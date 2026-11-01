#!/usr/bin/env bash
# Download every iCloud library and hand it to Immich.
# By default this also starts the hourly sync container.
#
#   ./import-icloud.sh you@icloud.com
#   ./import-icloud.sh --once you@icloud.com
#   ./import-icloud.sh --full you@icloud.com

set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

once=0
full=0
email=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --once) once=1 ;;
    --full) full=1 ;;
    --)
      shift
      break
      ;;
    -*)
      echo "unknown option: $1" >&2
      echo "usage: ./import-icloud.sh [--once] [--full] you@icloud.com" >&2
      exit 1
      ;;
    *)
      if [[ -n "$email" ]]; then
        echo "usage: ./import-icloud.sh [--once] [--full] you@icloud.com" >&2
        exit 1
      fi
      email="$1"
      ;;
  esac
  shift
done
if [[ ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "usage: ./import-icloud.sh [--once] [--full] you@icloud.com" >&2
  exit 1
fi

require_command docker docker
require_command python3 python3
load_env
require_api_key

mkdir -p "$ICLOUD_DOWNLOAD_LOCATION" "$ICLOUD_COOKIE_LOCATION"
set_env_var ICLOUD_USERNAME "$email"
ICLOUD_USERNAME="$email"
if [[ "$full" -eq 1 ]]; then
  export ICLOUD_FORCE_FULL=1
else
  export ICLOUD_FORCE_FULL=0
fi

echo "Stopping hourly sync if it is running, so it does not use the Apple session while we sign in."
docker compose --profile icloud stop icloud-sync >/dev/null 2>&1 || true

echo "Checking the Apple session."
echo "The Apple ID password and a 2FA code are requested only when the saved session is missing or expired."
echo "Do not pass the password as an argument."
icloudpd_args auth "$email"
docker compose --profile icloud run --rm -it icloud icloudpd "${ICLOUDPD_ARGS[@]}"

echo "Looking up iCloud libraries (PrimarySync and any SharedSync-* shared libraries)."
icloudpd_args list "$email"
list_err=$(mktemp)
set +e
list_output=$(docker compose --profile icloud run --rm -T icloud icloudpd "${ICLOUDPD_ARGS[@]}" 2>"$list_err")
list_code=$?
set -e
cat "$list_err" >&2
rm -f "$list_err"
if [[ "$list_code" -ne 0 ]]; then
  echo "Could not list iCloud libraries." >&2
  exit "$list_code"
fi
mapfile -t libraries < <(printf '%s\n' "$list_output" | icloud_parse_libraries | icloud_sort_libraries)
if [[ ${#libraries[@]} -eq 0 ]]; then
  echo "icloudpd --list-libraries did not name PrimarySync or a SharedSync library. Downloading PrimarySync." >&2
  printf '%s\n' "$list_output" >&2
  libraries=(PrimarySync)
fi
echo "Libraries: ${libraries[*]}"

migrate_flat_download_into_primary "$ICLOUD_DOWNLOAD_LOCATION"

for library in "${libraries[@]}"; do
  icloudpd_args download "$email" "$library"
  if [[ "$ICLOUDPD_INCREMENTAL" -eq 1 ]]; then
    echo "Incremental download of ${library}. Stops after ${ICLOUD_UNTIL_FOUND} photos already on disk."
  else
    echo "Full download of ${library} into $(icloud_library_dir_name "$library")/. This pass does not stop early."
  fi
  docker compose --profile icloud run --rm -it icloud icloudpd "${ICLOUDPD_ARGS[@]}"
  if [[ "$ICLOUDPD_INCREMENTAL" -eq 0 ]]; then
    mark_icloud_full_pass "$library"
    echo "Recorded a completed full pass for ${library}."
  fi
done

echo "Asking Immich to scan the iCloud folder."
IMMICH_URL="http://127.0.0.1:${IMMICH_PORT}" python3 scripts/icloud_library.py

docker compose up -d timeline

if [[ "$once" -eq 0 ]]; then
  echo "Starting the hourly iCloud sync."
  docker compose --profile icloud up -d --build icloud-sync
  echo "New photos in each library are checked every ${ICLOUD_SYNC_INTERVAL}s and then scanned into Immich."
  echo "Leave backup off in the Immich phone app. This sync is already the camera-roll copy."
else
  echo "One-shot import finished. Hourly sync was not started."
fi
