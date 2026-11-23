#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

if [[ "${EUID}" -ne 0 ]]; then
  printf 'Run with sudo: sudo ./scripts/update.sh\n' >&2
  exit 1
fi

[[ -d "$ROOT_DIR/.git" ]] || {
  printf 'ERROR: %s is not a Git checkout.\n' "$ROOT_DIR" >&2
  exit 1
}
[[ -n "$(git -C "$ROOT_DIR" remote)" ]] || {
  printf 'ERROR: Configure a Git remote before using the update script.\n' >&2
  exit 1
}
if [[ -n "$(git -C "$ROOT_DIR" status --porcelain)" ]]; then
  printf 'ERROR: Working tree has local changes. Commit or safely preserve them before updating.\n' >&2
  exit 1
fi

owner="${SUDO_USER:-root}"
if [[ "$owner" == root ]]; then
  git -C "$ROOT_DIR" pull --ff-only
else
  sudo -u "$owner" git -C "$ROOT_DIR" pull --ff-only
fi

exec "$ROOT_DIR/scripts/install.sh"
