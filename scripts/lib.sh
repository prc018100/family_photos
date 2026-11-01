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

# Fills ICLOUDPD_ARGS. mode is auth, download, or sync.
icloudpd_args() {
  local mode="$1"
  local email="$2"
  ICLOUDPD_ARGS=(
    --username "$email"
    --directory /data
    --cookie-directory /cookies
  )
  case "$mode" in
    auth)
      ICLOUDPD_ARGS+=(--auth-only)
      ;;
    download | sync)
      ICLOUDPD_ARGS+=(
        --folder-structure "{:%Y/%m}"
        --size original
        --live-photo-size original
        --until-found "${ICLOUD_UNTIL_FOUND:-10}"
      )
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
