#!/usr/bin/env bash
# Mount is already declared in Compose. This creates the Immich libraries
# for ~/sync/pictures and ~/sync/video (or whatever .env points at) and scans them.
#
#   ./add-sync-libraries.sh

set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

require_command docker docker
require_command python3 python3
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose (v2) is required." >&2
  exit 1
fi

load_env
require_api_key

if [[ -z "${SYNC_PICTURES_LOCATION:-}" || -z "${SYNC_VIDEO_LOCATION:-}" ]]; then
  cat >&2 <<'EOF'
Set both paths in .env, then recreate Immich so it sees the folders:

  SYNC_PICTURES_LOCATION=/home/funky/sync/pictures
  SYNC_VIDEO_LOCATION=/home/funky/sync/video

  docker compose up -d --force-recreate immich-server immich-machine-learning
  ./add-sync-libraries.sh

Leave them unset on a machine that does not have these folders.
EOF
  exit 1
fi

for path in "$SYNC_PICTURES_LOCATION" "$SYNC_VIDEO_LOCATION"; do
  if [[ ! -d "$path" ]]; then
    echo "Not a directory on this machine: $path" >&2
    exit 1
  fi
done

echo "Applying mounts and the machine-learning port. Compose recreates a container only when its config changed."
docker compose up -d immich-server immich-machine-learning
wait_for_healthy family_photos_immich_server 1800

IMMICH_URL="http://127.0.0.1:${IMMICH_PORT}" python3 scripts/icloud_library.py --sync-folders

echo "Pictures: ${SYNC_PICTURES_LOCATION} -> /mnt/media/sync-pictures (read-only)"
echo "Video:    ${SYNC_VIDEO_LOCATION} -> /mnt/media/sync-video (read-only)"
echo "Immich cannot change these files. To drop a duplicate, delete it in the sync folder; the next scan removes it here too."
