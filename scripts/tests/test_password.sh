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

icloudpd_args sync ada@icloud.com
joined="${ICLOUDPD_ARGS[*]}"
[[ "$joined" == *"--size original"* ]]
[[ "$joined" == *"--live-photo-size original"* ]]
[[ "$joined" == *"--until-found"* ]]
[[ "$joined" == *"--no-progress-bar"* ]]
[[ "$joined" != *"--auth-only"* ]]

echo "password and icloudpd args ok"
