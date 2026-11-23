#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE="sensehat-observatory.service"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Run with sudo: sudo ./scripts/restore.sh /path/to/backup\n' >&2
  exit 1
fi
backup_dir="${1:-}"
[[ -n "$backup_dir" && -d "$backup_dir" && ! -L "$backup_dir" ]] || {
  printf 'Supply an existing backup directory.\n' >&2
  exit 1
}
[[ "${CONFIRM_RESTORE:-}" == "YES" ]] || {
  printf 'Restore replaces Observatory Docker data and local config. Rerun with CONFIRM_RESTORE=YES.\n' >&2
  exit 1
}
[[ -f "$backup_dir/volumes.txt" && -f "$backup_dir/config/project.env" ]] || {
  printf 'Backup is incomplete.\n' >&2
  exit 1
}

compose() {
  docker compose --env-file "$ROOT_DIR/.env" \
    --project-directory "$ROOT_DIR" -f "$COMPOSE_FILE" "$@"
}

systemctl stop "$SERVICE" 2>/dev/null || true
compose stop 2>/dev/null || true

while IFS= read -r volume; do
  [[ "$volume" =~ ^sensehat-observatory_[a-zA-Z0-9_-]+$ ]] || {
    printf 'Unexpected volume name in backup: %s\n' "$volume" >&2
    exit 1
  }
  archive="$backup_dir/$volume.tar.gz"
  [[ -f "$archive" && ! -L "$archive" ]] || {
    printf 'Missing or invalid volume archive: %s\n' "$archive" >&2
    exit 1
  }
  if tar -tzf "$archive" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then
    printf 'Unsafe path found in archive %s\n' "$archive" >&2
    exit 1
  fi
  docker volume create "$volume" >/dev/null
  docker run --rm \
    -v "$volume:/volume" \
    -v "$backup_dir:/backup:ro" \
    alpine:3.20 \
    sh -c 'find /volume -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && tar -xzf "/backup/$1.tar.gz" -C /volume' sh "$volume"
done <"$backup_dir/volumes.txt"

install -m 0600 "$backup_dir/config/project.env" "$ROOT_DIR/.env"
install -o root -g sensehat-observatory -m 0640 \
  "$backup_dir/config/collector.env" /etc/sensehat-observatory/collector.env
install -o root -g root -m 0644 \
  "$backup_dir/config/observatory.yaml" /etc/sensehat-observatory/observatory.yaml
sensor_state="$backup_dir/config/sensor-state.tar.gz"
if [[ -f "$sensor_state" && ! -L "$sensor_state" ]]; then
  if tar -tzf "$sensor_state" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then
    printf 'Unsafe path found in sensor-state archive.\n' >&2
    exit 1
  fi
  tar -xzf "$sensor_state" -C /var/lib/sensehat-observatory
  chown -R sensehat-observatory:sensehat-observatory /var/lib/sensehat-observatory
fi

compose up -d --wait --wait-timeout 180
systemctl start "$SERVICE"
printf 'Restore completed from %s\n' "$backup_dir"
printf 'Review LAN_BIND_IP in .env to match the local interface address on this host.\n'
