#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

print_component() {
  local name="$1"
  local path="$2"
  if [[ -d "${path}/.git" || -f "${path}/.git" ]]; then
    printf '%s=%s\n' "${name}" "$(git -C "${path}" rev-parse HEAD)"
  else
    printf '%s=NOT_INITIALIZED\n' "${name}"
  fi
}

printf 'benchmark=%s\n' "$(git -C "${repo_root}" rev-parse HEAD)"
print_component mediamtx-playground "${repo_root}/components/mediamtx-playground"
print_component realtime-pose-triton "${repo_root}/components/realtime-pose-triton"
