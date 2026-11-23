#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE="sensehat-observatory.service"
COMPOSE_FILE="$ROOT_DIR/deploy/compose.yaml"

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Run with sudo: sudo ./scripts/backup.sh [/path/to/backup]\n' >&2
  exit 1
fi
[[ -f "$ROOT_DIR/.env" ]] || { printf 'Missing %s/.env\n' "$ROOT_DIR" >&2; exit 1; }

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="${1:-/var/backups/sensehat-observatory/$timestamp}"
install -d -m 0700 "$backup_dir"
chmod 0700 "$backup_dir"
compose() {
  docker compose --env-file "$ROOT_DIR/.env" \
    --project-directory "$ROOT_DIR" -f "$COMPOSE_FILE" "$@"
}

restore_services() {
  compose up -d >/dev/null 2>&1 || true
  systemctl start "$SERVICE" >/dev/null 2>&1 || true
}
trap restore_services EXIT

systemctl stop "$SERVICE"
container_ids="$(compose ps -q)"
[[ -n "$container_ids" ]] || { printf 'No Observatory containers are running.\n' >&2; exit 1; }
mapfile -t volumes < <(
  docker inspect $container_ids \
    --format '{{range .Mounts}}{{if eq .Type "volume"}}{{.Name}}{{"\n"}}{{end}}{{end}}' |
    sort -u
)
(( ${#volumes[@]} > 0 )) || { printf 'No persistent volumes were found.\n' >&2; exit 1; }
printf '%s\n' "${volumes[@]}" >"$backup_dir/volumes.txt"
compose stop

for volume in "${volumes[@]}"; do
  [[ "$volume" =~ ^sensehat-observatory_[a-zA-Z0-9_-]+$ ]] || {
    printf 'Unexpected Docker volume name: %s\n' "$volume" >&2
    exit 1
  }
  docker run --rm \
    -v "$volume:/volume:ro" \
    -v "$backup_dir:/backup" \
    alpine:3.20 \
    tar -czf "/backup/$volume.tar.gz" -C /volume .
done

install -d -m 0700 "$backup_dir/config"
install -m 0600 "$ROOT_DIR/.env" "$backup_dir/config/project.env"
install -m 0600 /etc/sensehat-observatory/collector.env "$backup_dir/config/collector.env"
install -m 0600 /etc/sensehat-observatory/observatory.yaml "$backup_dir/config/observatory.yaml"
tar -czf "$backup_dir/config/sensor-state.tar.gz" \
  -C /var/lib/sensehat-observatory .
printf 'Backup created at %s\n' "$backup_dir"
printf 'This backup contains secrets; keep it private and off public shares.\n'
