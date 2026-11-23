#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
SERVICE_USER="sensehat-observatory"
OPT_DIR="/opt/sensehat-observatory"
ETC_DIR="/etc/sensehat-observatory"
SERVICE_FILE="/etc/systemd/system/sensehat-observatory.service"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

as_root() {
  [[ "${EUID}" -eq 0 ]] || fail "Run this installer with sudo: sudo ./scripts/install.sh"
}

require_raspberry_pi() {
  [[ -r /proc/device-tree/model ]] || fail "This installer must run on the Raspberry Pi deployment target."
  local model
  model="$(tr -d '\0' </proc/device-tree/model)"
  [[ "$model" == *"Raspberry Pi"* ]] || fail "Detected device is not a Raspberry Pi: $model"
  [[ -r /etc/os-release ]] || fail "Cannot identify operating system."
  . /etc/os-release
  [[ "${ID:-}" == "debian" || "${ID:-}" == "raspbian" ]] || fail "Use Raspberry Pi OS (Debian-based); detected ${PRETTY_NAME:-unknown}."
  [[ "$(uname -m)" == "aarch64" ]] || fail "This stack requires 64-bit Raspberry Pi OS (aarch64); InfluxDB 2.x does not publish an ARMv7 image."
}

install_prerequisites() {
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker.io python3 python3-venv python3-sense-hat \
    iproute2 openssl ca-certificates
  if ! docker compose version >/dev/null 2>&1; then
    local compose_package=""
    for candidate in docker-compose-v2 docker-compose-plugin; do
      if apt-cache show "$candidate" >/dev/null 2>&1; then
        compose_package="$candidate"
        break
      fi
    done
    [[ -n "$compose_package" ]] || fail "No Docker Compose v2 package is available from configured Raspberry Pi OS repositories."
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$compose_package"
  fi
  docker compose version >/dev/null || fail "Docker Compose v2 is unavailable."
  systemctl enable --now docker.service
}

read_env_value() {
  local key="$1"
  local file="$2"
  local value
  value="$(sed -n "s/^${key}=//p" "$file" | head -n 1)"
  [[ -n "$value" ]] || fail "Missing $key in $file"
  printf '%s' "$value"
}

validate_port() {
  local key="$1"
  local port="$2"
  local lan_ip="$3"
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || fail "$key must be a numeric TCP port."
  (( port >= 1 && port <= 65535 )) || fail "$key is outside the TCP port range."
  case "$port" in
    53|80|443) fail "$key cannot use commonly reserved DNS/HTTP/HTTPS port $port." ;;
  esac
  local already_owned=false
  local container_ids
  container_ids="$(compose ps -q 2>/dev/null || true)"
  if [[ -n "$container_ids" ]]; then
    already_owned="$(docker inspect $container_ids | python3 -c '
import json,sys
port, address = sys.argv[1:]
containers = json.load(sys.stdin)
print(str(any(
    binding.get("HostPort") == port and binding.get("HostIp") == address
    for container in containers
    for bindings in (container.get("NetworkSettings", {}).get("Ports") or {}).values()
    if bindings
    for binding in bindings
)).lower())
' "$port" "$lan_ip")"
  fi
  if ss -H -ltn "sport = :$port" | grep -q . && [[ "$already_owned" != "true" ]]; then
    fail "TCP port $port is already listening. Choose another value in .env; no service was changed."
  fi
}

create_environment_file() {
  local env_file="$ROOT_DIR/.env"
  if [[ ! -e "$env_file" ]]; then
    local interface bind_address
    interface="$(ip -4 route show default | awk 'NR == 1 {for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}')"
    [[ -n "$interface" ]] || fail "Could not determine the default network interface. Configure networking, then rerun."
    bind_address="$(ip -4 -o addr show dev "$interface" scope global | awk 'NR == 1 {split($4,a,"/"); print a[1]}')"
    [[ -n "$bind_address" ]] || fail "Could not determine a local IPv4 address on $interface. Configure networking, then rerun."
    local influx_password influx_token grafana_password
    influx_password="$(openssl rand -hex 32)"
    influx_token="$(openssl rand -hex 32)"
    grafana_password="$(openssl rand -hex 32)"
    umask 077
    cat >"$env_file" <<EOF
LAN_BIND_IP=$bind_address
GRAFANA_PORT=3000
INFLUX_PORT=8086
DOCKER_INFLUXDB_INIT_USERNAME=observatory
DOCKER_INFLUXDB_INIT_PASSWORD=$influx_password
DOCKER_INFLUXDB_INIT_ORG=sensehat-observatory
DOCKER_INFLUXDB_INIT_BUCKET=environment
DOCKER_INFLUXDB_INIT_ADMIN_TOKEN=$influx_token
GF_SECURITY_ADMIN_USER=admin
GF_SECURITY_ADMIN_PASSWORD=$grafana_password
INFLUX_URL=http://$bind_address:8086
INFLUX_ORG=sensehat-observatory
INFLUX_BUCKET=environment
INFLUX_TOKEN=$influx_token
EOF
    chmod 600 "$env_file"
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
      chown "$SUDO_USER:$(id -gn "$SUDO_USER")" "$env_file"
    fi
  fi
  [[ -f "$env_file" && ! -L "$env_file" ]] || fail "$env_file must be a regular file, not a symlink."
  local token password grafana_password
  token="$(read_env_value DOCKER_INFLUXDB_INIT_ADMIN_TOKEN "$env_file")"
  password="$(read_env_value DOCKER_INFLUXDB_INIT_PASSWORD "$env_file")"
  grafana_password="$(read_env_value GF_SECURITY_ADMIN_PASSWORD "$env_file")"
  [[ "$token" != *replace-* && "$password" != *replace-* && "$grafana_password" != *replace-* ]] || fail "Replace example credentials in .env or remove it and rerun to generate secrets."
  chmod 600 "$env_file"
}

prepare_service_user() {
  local groups=()
  getent group i2c >/dev/null && groups+=(i2c)
  getent group input >/dev/null && groups+=(input)
  getent group video >/dev/null && groups+=(video)
  if [[ ! -e /dev/i2c-1 ]]; then
    fail "I2C device /dev/i2c-1 is unavailable. Enable I2C and attach the Sense HAT, reboot, then rerun."
  fi
  if [[ ! -e /dev/input ]]; then
    fail "Linux input devices are unavailable; verify the Sense HAT joystick input support."
  fi
  getent group i2c >/dev/null || fail "The i2c group is unavailable; check Raspberry Pi OS I2C configuration."
  getent group input >/dev/null || fail "The input group is unavailable; check Raspberry Pi OS joystick support."
  getent group video >/dev/null || fail "The video group is unavailable; check Raspberry Pi OS framebuffer permissions."
  local group_list
  group_list="$(IFS=,; printf '%s' "${groups[*]}")"
  if ! getent group "$SERVICE_USER" >/dev/null; then
    groupadd --system "$SERVICE_USER"
  fi
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /var/lib/sensehat-observatory \
      --gid "$SERVICE_USER" \
      --shell /usr/sbin/nologin --groups "$group_list" "$SERVICE_USER"
  else
    usermod --home /var/lib/sensehat-observatory --gid "$SERVICE_USER" \
      --append --groups "$group_list" "$SERVICE_USER"
  fi
  install -d -o root -g "$SERVICE_USER" -m 0750 "$ETC_DIR"
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
    /var/lib/sensehat-observatory
}

prepare_python() {
  install -d -o root -g root -m 0755 "$OPT_DIR"
  install -d -o root -g root -m 0755 "$OPT_DIR/src"
  install -d -o root -g root -m 0755 "$OPT_DIR/src/sensehat_observatory"
  cp -a "$ROOT_DIR/src/sensehat_observatory/." "$OPT_DIR/src/sensehat_observatory/"
  install -m 0644 "$ROOT_DIR/pyproject.toml" "$OPT_DIR/pyproject.toml"
  install -m 0644 "$ROOT_DIR/README.md" "$OPT_DIR/README.md"
  install -m 0644 "$ROOT_DIR/LICENSE" "$OPT_DIR/LICENSE"
  python3 -m venv --system-site-packages "$OPT_DIR/venv"
  "$OPT_DIR/venv/bin/pip" install --upgrade pip
  "$OPT_DIR/venv/bin/pip" install "$OPT_DIR"
  chown -R root:root "$OPT_DIR"
}

preflight_hardware() {
  runuser -u "$SERVICE_USER" -- \
    "$OPT_DIR/venv/bin/python" "$ROOT_DIR/scripts/check_hardware.py"
}

prepare_config_and_service() {
  if [[ ! -e "$ETC_DIR/observatory.yaml" ]]; then
    install -m 0644 "$ROOT_DIR/config/observatory.example.yaml" "$ETC_DIR/observatory.yaml"
  fi
  install -o root -g "$SERVICE_USER" -m 0640 "$ROOT_DIR/.env" "$ETC_DIR/collector.env"
  install -o root -g root -m 0644 "$ROOT_DIR/systemd/sensehat-observatory.service" "$SERVICE_FILE"
  systemctl daemon-reload
}

compose() {
  docker compose --env-file "$ROOT_DIR/.env" \
    --project-directory "$ROOT_DIR" \
    -f "$ROOT_DIR/deploy/compose.yaml" "$@"
}

ensure_bucket() {
  local bucket="$1"
  local retention="$2"
  local org="$3"
  local token="$4"
  local json bucket_id
  json="$(compose exec -T influxdb influx bucket list --json --org "$org" --token "$token")"
  bucket_id="$(printf '%s' "$json" | python3 -c 'import json,sys; name=sys.argv[1]; print(next((b["id"] for b in json.load(sys.stdin) if b["name"] == name), ""))' "$bucket")"
  if [[ -z "$bucket_id" ]]; then
    compose exec -T influxdb influx bucket create --name "$bucket" --retention "$retention" --org "$org" --token "$token" >/dev/null
  else
    compose exec -T influxdb influx bucket update --id "$bucket_id" --retention "$retention" --org "$org" --token "$token" >/dev/null
  fi
}

deploy_stack() {
  local lan_ip grafana_port influx_port org token
  lan_ip="$(read_env_value LAN_BIND_IP "$ROOT_DIR/.env")"
  grafana_port="$(read_env_value GRAFANA_PORT "$ROOT_DIR/.env")"
  influx_port="$(read_env_value INFLUX_PORT "$ROOT_DIR/.env")"
  validate_port GRAFANA_PORT "$grafana_port" "$lan_ip"
  validate_port INFLUX_PORT "$influx_port" "$lan_ip"
  [[ "$grafana_port" != "$influx_port" ]] || fail "Grafana and InfluxDB cannot use the same host port."
  [[ "$lan_ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "LAN_BIND_IP must be an IPv4 address."
  python3 -c '
import ipaddress,sys
address=ipaddress.ip_address(sys.argv[1])
if not any(address in ipaddress.ip_network(network) for network in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")):
    raise SystemExit(1)
' "$lan_ip" || fail "LAN_BIND_IP must be an RFC1918 private IPv4 address to avoid public binding."
  ip -4 -o addr show | awk '{split($4,a,"/"); print a[1]}' | grep -Fxq "$lan_ip" || fail "LAN_BIND_IP $lan_ip is not assigned to a Pi interface."

  python3 "$ROOT_DIR/scripts/generate_dashboard.py"
  compose config --quiet
  compose up -d --wait --wait-timeout 180
  org="$(read_env_value DOCKER_INFLUXDB_INIT_ORG "$ROOT_DIR/.env")"
  token="$(read_env_value DOCKER_INFLUXDB_INIT_ADMIN_TOKEN "$ROOT_DIR/.env")"
  mapfile -t retention_days < <(
    "$OPT_DIR/venv/bin/python" -c '
from sensehat_observatory.config import load_config
import sys
config = load_config(sys.argv[1], environ={})
for days in (
    config.retention.environment_days,
    config.retention.motion_raw_days,
    config.retention.motion_aggregate_days,
    config.retention.hardware_events_days,
    config.retention.health_days,
):
    print(f"{days}d")
' "$ETC_DIR/observatory.yaml"
  )
  (( ${#retention_days[@]} == 5 )) || fail "Could not read all retention settings from observatory.yaml."
  ensure_bucket environment "${retention_days[0]}" "$org" "$token"
  ensure_bucket motion_raw "${retention_days[1]}" "$org" "$token"
  ensure_bucket motion_aggregate "${retention_days[2]}" "$org" "$token"
  ensure_bucket events "${retention_days[3]}" "$org" "$token"
  ensure_bucket health "${retention_days[4]}" "$org" "$token"
}

main() {
  as_root
  require_raspberry_pi
  install_prerequisites
  create_environment_file
  prepare_service_user
  prepare_python
  preflight_hardware
  prepare_config_and_service
  deploy_stack
  systemctl enable sensehat-observatory.service
  if systemctl is-active --quiet sensehat-observatory.service; then
    systemctl restart sensehat-observatory.service
  else
    systemctl start sensehat-observatory.service
  fi

  local lan_ip grafana_port
  lan_ip="$(read_env_value LAN_BIND_IP "$ROOT_DIR/.env")"
  grafana_port="$(read_env_value GRAFANA_PORT "$ROOT_DIR/.env")"
  printf '\nSenseHAT Observatory is installed.\n'
  printf 'Dashboard: http://%s:%s (credentials in .env)\n' "$lan_ip" "$grafana_port"
  printf 'Collector logs: sudo journalctl -u sensehat-observatory -f\n'
  printf 'Review location metadata in %s/observatory.yaml\n' "$ETC_DIR"
  printf 'No existing DNS, web-server, firewall or router configuration was changed.\n'
}

main "$@"
