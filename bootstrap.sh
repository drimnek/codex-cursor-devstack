#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MODE="deploy"

usage() {
  cat <<'USAGE'
Usage: ./bootstrap.sh [--check]

  --check   Prepare bootstrap validation dependencies if needed, run the
            preflight and Ansible syntax validation, then stop before applying
            the platform playbook.
USAGE
}

log() {
  printf '[bootstrap] %s\n' "$*"
}

fail() {
  printf '[bootstrap] ERROR: %s\n' "$*" >&2
  exit 1
}

case "${1:-}" in
  "") ;;
  --check) MODE="check" ;;
  -h|--help) usage; exit 0 ;;
  *) usage >&2; fail "Unknown argument: $1" ;;
esac

resolve_controller_user() {
  if [[ ${EUID} -eq 0 ]]; then
    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
      printf '%s\n' "$SUDO_USER"
      return
    fi
    if [[ -n "${CONTROLLER_USER:-}" && "${CONTROLLER_USER}" != "root" ]]; then
      printf '%s\n' "$CONTROLLER_USER"
      return
    fi
    fail "Do not run directly as root without an operator identity. Use sudo from the intended operator account or set CONTROLLER_USER explicitly."
  fi

  id -un
}

CONTROLLER_USER="$(resolve_controller_user)"

run_root() {
  if [[ ${EUID} -eq 0 ]]; then
    "$@"
  else
    command -v sudo >/dev/null 2>&1 || fail "sudo is required for deployment but is not installed."
    sudo "$@"
  fi
}

check_supported_os() {
  [[ -r /etc/os-release ]] || fail "Cannot identify the host OS: /etc/os-release is missing."
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
    fail "This bootstrap is intentionally gated to Ubuntu 22.04; detected ${ID:-unknown} ${VERSION_ID:-unknown}."
  fi
  log "Host OS: Ubuntu ${VERSION_ID}."
}

check_source_tree() {
  local required=(
    "ansible/inventory.ini"
    "ansible/playbook.yml"
    "platform-src/bin/agentctl"
    "platform-src/bin/agentd"
    "platform-src/containers/Containerfile.base"
    "platform-src/containers/Containerfile.codex"
    "platform-src/containers/Containerfile.cursor"
  )
  local rel
  for rel in "${required[@]}"; do
    [[ -f "$HERE/$rel" ]] || fail "Required deployment source is missing: $rel"
  done
  log "Deployment source tree: OK."
}

check_controller_user() {
  getent passwd "$CONTROLLER_USER" >/dev/null || fail "Operator user does not exist: $CONTROLLER_USER"
  [[ "$CONTROLLER_USER" != "root" ]] || fail "The operator identity must not be root."
  log "Operator identity: $CONTROLLER_USER."
}

check_bootstrap_tools() {
  command -v bash >/dev/null 2>&1 || fail "bash is required."
  command -v getent >/dev/null 2>&1 || fail "getent is required."
  command -v apt-get >/dev/null 2>&1 || fail "apt-get is required for bootstrap dependency installation."
  if [[ ${EUID} -ne 0 ]]; then
    command -v sudo >/dev/null 2>&1 || fail "sudo is required to install bootstrap dependencies."
  fi
}

preflight() {
  log "Running preflight checks..."
  check_supported_os
  check_source_tree
  check_controller_user
  check_bootstrap_tools
  log "Preflight checks passed."
}

ensure_ansible() {
  if command -v ansible-playbook >/dev/null 2>&1; then
    log "Ansible already installed: $(ansible-playbook --version | head -n 1)."
    return
  fi

  log "Ansible is not installed; installing the bootstrap dependency from Ubuntu repositories..."
  run_root apt-get update
  if ! run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ansible-core; then
    run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y ansible
  fi

  command -v ansible-playbook >/dev/null 2>&1 || fail "Ansible installation completed without providing ansible-playbook."
  log "Installed: $(ansible-playbook --version | head -n 1)."
}

ansible_syntax_check() {
  command -v ansible-playbook >/dev/null 2>&1 || return 2

  log "Validating Ansible deployment syntax..."
  ansible-playbook \
    --syntax-check \
    -i "$HERE/ansible/inventory.ini" \
    "$HERE/ansible/playbook.yml" \
    -e "controller_user=$CONTROLLER_USER" \
    -e "bootstrap_source=$HERE/platform-src"
  log "Ansible syntax check passed."
}

run_deployment() {
  log "Starting host deployment..."
  run_root ansible-playbook \
    -i "$HERE/ansible/inventory.ini" \
    "$HERE/ansible/playbook.yml" \
    -e "controller_user=$CONTROLLER_USER" \
    -e "bootstrap_source=$HERE/platform-src"
}

preflight
ensure_ansible
ansible_syntax_check

if [[ "$MODE" == "check" ]]; then
  log "Check-only mode completed. Bootstrap validation dependencies are ready and the platform was not deployed."
  exit 0
fi

run_deployment

log "Bootstrap completed. Log out and back in before normal agentctl use if this run added you to agentdev-ops."
