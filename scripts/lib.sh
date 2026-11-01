# Shared helpers for setup.sh and import-icloud.sh. No side effects on source.

generate_db_password() {
  # 32 hex characters. Letters and digits only, which is what Immich asks for.
  # Uses /dev/urandom so openssl is not required.
  od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
}

password_is_placeholder() {
  local line
  line=$(grep -E '^DB_PASSWORD=' .env | head -n 1 || true)
  [[ "$line" == "DB_PASSWORD=" || "$line" == "DB_PASSWORD=changeme" ]]
}

postgres_initialized() {
  [[ -f data/postgres/PG_VERSION ]]
}

set_env_var() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env
  fi
}

load_env() {
  if [[ ! -f .env ]]; then
    echo "Missing .env. Run ./setup.sh first." >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  : "${ICLOUD_DOWNLOAD_LOCATION:=./data/icloud}"
  : "${ICLOUD_COOKIE_LOCATION:=./data/icloud-session}"
  : "${IMMICH_PORT:=2283}"
  : "${TIMELINE_PORT:=2284}"
  : "${ICLOUD_UNTIL_FOUND:=10}"
  : "${ICLOUD_SYNC_INTERVAL:=3600}"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing $1. On Fedora: sudo dnf install $2" >&2
    exit 1
  fi
}

require_api_key() {
  if [[ -z "${IMMICH_API_KEY:-}" ]]; then
    cat >&2 <<'EOF'
IMMICH_API_KEY is empty.

Open Immich as the admin, create an API key, and set IMMICH_API_KEY in .env.
The timeline needs asset.read, asset.view, and person.read.
This import also needs user.read, library.read, library.create, and library.update.
Permission "all" covers those and is fine on this private server.
EOF
    exit 1
  fi
}

# Cookie dir on the host, or /cookies inside the download containers.
icloud_cookie_dir() {
  if [[ -n "${ICLOUD_COOKIE_LOCATION:-}" ]]; then
    printf '%s' "$ICLOUD_COOKIE_LOCATION"
  elif [[ -d /cookies ]]; then
    printf '%s' /cookies
  else
    printf '%s' "./data/icloud-session"
  fi
}

# Filesystem-safe folder for one iCloud library. PrimarySync and SharedSync-*
# stay readable; anything else becomes an underscore.
icloud_library_dir_name() {
  local cleaned
  cleaned=$(printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_')
  cleaned="${cleaned##_}"
  cleaned="${cleaned%%_}"
  if [[ -z "$cleaned" ]]; then
    cleaned="library"
  fi
  printf '%s' "$cleaned"
}

# One marker per library, written only after a full pass (no --until-found) exits 0.
icloud_full_pass_marker() {
  printf '%s/full-import-complete/%s' "$(icloud_cookie_dir)" "$(icloud_library_dir_name "$1")"
}

icloud_full_pass_done() {
  [[ -f "$(icloud_full_pass_marker "$1")" ]]
}

mark_icloud_full_pass() {
  local marker
  marker=$(icloud_full_pass_marker "$1")
  mkdir -p "$(dirname "$marker")"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$marker"
}

# Keep library names icloudpd actually prints: PrimarySync and SharedSync-*.
icloud_parse_libraries() {
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    if [[ "$line" == "PrimarySync" || "$line" == SharedSync-* ]]; then
      printf '%s\n' "$line"
    fi
  done
}

# PrimarySync first, then shared libraries in a stable order.
icloud_sort_libraries() {
  local line
  local -a rest=()
  local have_primary=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    if [[ "$line" == "PrimarySync" ]]; then
      have_primary=1
    else
      rest+=("$line")
    fi
  done
  if [[ "$have_primary" -eq 1 ]]; then
    printf '%s\n' PrimarySync
  fi
  if [[ ${#rest[@]} -gt 0 ]]; then
    printf '%s\n' "${rest[@]}" | LC_ALL=C sort -u
  fi
}

# An interrupted pre-subfolder download lives in year directories at the
# iCloud root. Move those under PrimarySync once, so the full pass skips
# files already saved and shared libraries cannot collide with them.
migrate_flat_download_into_primary() {
  local root="$1"
  local dest="$root/PrimarySync"
  local entry base moved=0
  [[ -d "$root" ]] || return 0
  [[ -d "$dest" ]] && return 0
  shopt -s nullglob
  for entry in "$root"/*; do
    base=$(basename "$entry")
    if [[ "$base" =~ ^[0-9]{4}$ && -d "$entry" ]]; then
      mkdir -p "$dest"
      mv "$entry" "$dest/"
      moved=1
    fi
  done
  shopt -u nullglob
  if [[ "$moved" -eq 1 ]]; then
    echo "Moved earlier year folders into ${dest}/ so those files are not downloaded again."
  fi
}

# Fills ICLOUDPD_ARGS. mode is auth, list, download, or sync.
# download and sync take the library name as the third argument.
# Sets ICLOUDPD_INCREMENTAL=1 only when --until-found is included.
icloudpd_args() {
  local mode="$1"
  local email="$2"
  local library="${3:-}"
  ICLOUDPD_INCREMENTAL=0
  ICLOUDPD_ARGS=(
    --username "$email"
    --cookie-directory /cookies
  )
  case "$mode" in
    auth)
      ICLOUDPD_ARGS+=(--auth-only --directory /data)
      ;;
    list)
      ICLOUDPD_ARGS+=(--list-libraries --directory /data --no-progress-bar)
      ;;
    download | sync)
      if [[ -z "$library" ]]; then
        echo "icloudpd_args $mode needs a library name" >&2
        return 1
      fi
      ICLOUDPD_ARGS+=(
        --library "$library"
        --directory "/data/$(icloud_library_dir_name "$library")"
        --folder-structure "{:%Y/%m}"
        --size original
        --live-photo-size original
      )
      if [[ "${ICLOUD_FORCE_FULL:-0}" != "1" ]] && icloud_full_pass_done "$library"; then
        ICLOUDPD_ARGS+=(--until-found "${ICLOUD_UNTIL_FOUND:-10}")
        ICLOUDPD_INCREMENTAL=1
      fi
      ;;
    *)
      echo "unknown icloudpd mode: $mode" >&2
      return 1
      ;;
  esac
  if [[ "$mode" == sync ]]; then
    ICLOUDPD_ARGS+=(--no-progress-bar)
  fi
}

wait_for_healthy() {
  local name="$1"
  local timeout="$2"
  local start=$SECONDS
  local status
  while ((SECONDS - start < timeout)); do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$name" 2>/dev/null || echo missing)
    if [[ "$status" == "healthy" ]]; then
      echo "$name is healthy"
      return 0
    fi
    printf '  waiting for %s (%s)\n' "$name" "$status"
    sleep 10
  done
  echo "$name did not become healthy within ${timeout}s." >&2
  echo "Logs: docker compose logs $name" >&2
  return 1
}
