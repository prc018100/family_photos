#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/lib.sh

password=$(generate_db_password)
if [[ ! "$password" =~ ^[0-9a-f]{32}$ ]]; then
  echo "expected 32 hex chars, got: $password" >&2
  exit 1
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
cd "$tmp"
printf 'DB_PASSWORD=changeme\n' > .env
if ! password_is_placeholder; then
  echo "changeme should count as a placeholder" >&2
  exit 1
fi
set_env_var DB_PASSWORD "$password"
if password_is_placeholder; then
  echo "updated password still looks like a placeholder" >&2
  exit 1
fi
grep -qx "DB_PASSWORD=${password}" .env

mkdir -p data/postgres
if postgres_initialized; then
  echo "empty postgres dir should not look initialized" >&2
  exit 1
fi
touch data/postgres/PG_VERSION
if ! postgres_initialized; then
  echo "PG_VERSION should count as initialized" >&2
  exit 1
fi

export ICLOUD_COOKIE_LOCATION="$tmp/cookies"
export ICLOUD_FORCE_FULL=0

icloudpd_args sync ada@icloud.com PrimarySync
joined="${ICLOUDPD_ARGS[*]}"
[[ "$joined" == *"--size original"* ]]
[[ "$joined" == *"--live-photo-size original"* ]]
[[ "$joined" == *"--library PrimarySync"* ]]
[[ "$joined" == *"--directory /data/PrimarySync"* ]]
[[ "$joined" != *"--until-found"* ]]
[[ "$joined" == *"--no-progress-bar"* ]]
[[ "$ICLOUDPD_INCREMENTAL" -eq 0 ]]

mark_icloud_full_pass PrimarySync
[[ -f "$tmp/cookies/full-import-complete/PrimarySync" ]]
icloudpd_args sync ada@icloud.com PrimarySync
joined="${ICLOUDPD_ARGS[*]}"
[[ "$joined" == *"--until-found"* ]]
[[ "$ICLOUDPD_INCREMENTAL" -eq 1 ]]

export ICLOUD_FORCE_FULL=1
icloudpd_args download ada@icloud.com PrimarySync
joined="${ICLOUDPD_ARGS[*]}"
[[ "$joined" != *"--until-found"* ]]
[[ "$joined" != *"--no-progress-bar"* ]]
[[ "$ICLOUDPD_INCREMENTAL" -eq 0 ]]

icloudpd_args sync ada@icloud.com "SharedSync-Family Album"
joined="${ICLOUDPD_ARGS[*]}"
[[ "$joined" == *"--directory /data/SharedSync-Family_Album"* ]]
[[ "$joined" == *"--library SharedSync-Family Album"* ]]
[[ ! -f "$tmp/cookies/full-import-complete/SharedSync-Family_Album" ]]

printf '%s\n' \
  "Authentication completed successfully" \
  "SharedSync-Zed" \
  "PrimarySync" \
  "SharedSync-Amy" \
  "SomethingElse" \
  | icloud_parse_libraries | icloud_sort_libraries > "$tmp/libs"
printf '%s\n' PrimarySync SharedSync-Amy SharedSync-Zed > "$tmp/expect"
diff -u "$tmp/expect" "$tmp/libs"

root="$tmp/photos"
mkdir -p "$root/2024/03" "$root/2019"
touch "$root/2024/03/a.jpg" "$root/keep.txt"
migrate_flat_download_into_primary "$root"
[[ -f "$root/PrimarySync/2024/03/a.jpg" ]]
[[ -d "$root/PrimarySync/2019" ]]
[[ -f "$root/keep.txt" ]]
[[ ! -d "$root/2024" ]]
mkdir -p "$root/1999"
migrate_flat_download_into_primary "$root"
[[ -d "$root/1999" ]]

echo "password and icloudpd args ok"
