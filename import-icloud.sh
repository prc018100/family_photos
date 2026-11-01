#!/usr/bin/env bash
# Download an iCloud library and hand it to Immich.
# By default this also starts the hourly sync container.
#
#   ./import-icloud.sh you@icloud.com
#   ./import-icloud.sh --once you@icloud.com

set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

once=0
if [[ "${1:-}" == "--once" ]]; then
  once=1
  shift
fi
email="${1:-}"
if [[ ! "$email" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
  echo "usage: ./import-icloud.sh [--once] you@icloud.com" >&2
  exit 1
fi

require_command docker docker
require_command python3 python3
load_env
require_api_key

mkdir -p "$ICLOUD_DOWNLOAD_LOCATION" "$ICLOUD_COOKIE_LOCATION"
set_env_var ICLOUD_USERNAME "$email"
# The value was just written; export it for this process too.
ICLOUD_USERNAME="$email"

echo "Stopping hourly sync if it is running, so it does not use the Apple session while we sign in."
docker compose --profile icloud stop icloud-sync >/dev/null 2>&1 || true

echo "Checking the Apple session."
echo "The Apple ID password and a 2FA code are requested only when the saved session is missing or expired."
echo "Do not pass the password as an argument."
icloudpd_args auth "$email"
docker compose --profile icloud run --rm -it icloud icloudpd "${ICLOUDPD_ARGS[@]}"

echo "Downloading originals, including Live Photos. Files already saved here are skipped."
icloudpd_args download "$email"
docker compose --profile icloud run --rm -it icloud icloudpd "${ICLOUDPD_ARGS[@]}"

echo "Asking Immich to scan the iCloud folder."
IMMICH_URL="http://127.0.0.1:${IMMICH_PORT}" python3 scripts/icloud_library.py

docker compose up -d timeline

if [[ "$once" -eq 0 ]]; then
  echo "Starting the hourly iCloud sync."
  docker compose --profile icloud up -d --build icloud-sync
  echo "New iCloud photos are checked every ${ICLOUD_SYNC_INTERVAL}s and then scanned into Immich."
  echo "Leave backup off in the Immich phone app. This sync is already the camera-roll copy."
else
  echo "One-shot import finished. Hourly sync was not started."
fi
