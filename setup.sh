#!/usr/bin/env bash
# First start: .env, database password, directories, then wait out Immich's
# first-boot map-data import before calling the stack healthy.

set -euo pipefail
cd "$(dirname "$0")"
source scripts/lib.sh

require_command docker docker
require_command python3 python3
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose (v2) is required. On Fedora: sudo dnf install docker-compose-plugin" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

if password_is_placeholder && ! postgres_initialized; then
  set_env_var DB_PASSWORD "$(generate_db_password)"
  echo "Wrote a new DB_PASSWORD into .env"
elif password_is_placeholder && postgres_initialized; then
  echo "DB_PASSWORD is still the placeholder, and Postgres already has data. Leaving the password unchanged." >&2
fi

load_env
mkdir -p data/library data/postgres \
  "$ICLOUD_DOWNLOAD_LOCATION" "$ICLOUD_COOKIE_LOCATION" \
  external/library1 external/library2 external/library3

echo "Starting Immich and the timeline."
echo "The first start imports map data before Immich answers. That often takes several minutes."
docker compose up -d --build

wait_for_healthy family_photos_immich_server 1800
wait_for_healthy family_photos_timeline 300

python3 - <<'PY'
import os
from urllib.parse import urlparse, urlunparse
public = os.environ.get("IMMICH_PUBLIC_URL", "http://192.168.1.3:2283")
parsed = urlparse(public)
host = parsed.hostname or "192.168.1.3"
timeline_port = os.environ.get("TIMELINE_PORT", "2284")
timeline = urlunparse((parsed.scheme or "http", f"{host}:{timeline_port}", "", "", "", ""))
print()
print("Stack is up.")
print(f"  Immich:   {public}")
print(f"  Timeline: {timeline}")
print()
print("Next:")
print(f"  1. Open {public} and create the admin account. The first user is the admin.")
print("  2. Create an API key with asset.read, asset.view, person.read,")
print("     user.read, library.read, library.create, and library.update.")
print('     Permission "all" is fine on this private server.')
print("     Put it in .env as IMMICH_API_KEY.")
print("  3. ./import-icloud.sh you@icloud.com")
print()
print("That import keeps the originals in iCloud and copies new photos every hour.")
print("In the Immich phone app, leave backup off so the camera roll is not imported twice.")
PY
