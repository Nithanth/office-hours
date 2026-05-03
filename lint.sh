#!/usr/bin/env bash
# Format, lint, and type-check Lux Scholar.
#
# Usage:
#   ./lint.sh            # apply safe Ruff fixes, format, then run mypy
#   ./lint.sh --check    # verify formatting/lint/types without changing files
#   ./lint.sh src tests  # run against specific Ruff/mypy targets

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v tput >/dev/null 2>&1 && [[ -t 1 ]]; then
  GREEN="$(tput setaf 2)"
  RED="$(tput setaf 1)"
  CYAN="$(tput setaf 6)"
  RESET="$(tput sgr0)"
else
  GREEN=""
  RED=""
  CYAN=""
  RESET=""
fi

mode="fix"
if [[ "${1:-}" == "--check" ]]; then
  mode="check"
  shift
fi

targets=("$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  targets=(".")
fi

for target in "${targets[@]}"; do
  if [[ -f "$target" && "$target" != *.py && "$target" != *.pyi ]]; then
    printf '%sUnsupported lint target: %s%s\n' "$RED" "$target" "$RESET" >&2
    printf 'lint.sh runs Python tooling; pass Python files/directories or no targets.\n' >&2
    exit 2
  fi
done

resolve_tool() {
  local tool="$1"
  if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/$tool" ]]; then
    printf '%s\n' "$VIRTUAL_ENV/bin/$tool"
  elif [[ -x ".venv/bin/$tool" ]]; then
    printf '%s\n' ".venv/bin/$tool"
  elif [[ -x "venv/bin/$tool" ]]; then
    printf '%s\n' "venv/bin/$tool"
  elif command -v "$tool" >/dev/null 2>&1; then
    command -v "$tool"
  else
    printf '%sMissing required tool: %s%s\n' "$RED" "$tool" "$RESET" >&2
    printf 'Install dev dependencies with: pip install -r requirements-dev.txt\n' >&2
    exit 127
  fi
}

RUFF="$(resolve_tool ruff)"
MYPY="$(resolve_tool mypy)"

run_step() {
  local cmd=("$@")
  printf '%b\n────────────────────────────────────────────────────────\n▶ %s\n────────────────────────────────────────────────────────%b\n' \
    "$CYAN" "${cmd[*]}" "$RESET"
  if "${cmd[@]}"; then
    printf '%b✔ Successfully finished: %s%b\n' "$GREEN" "${cmd[*]}" "$RESET"
  else
    printf '%b✖ Failed: %s%b\n' "$RED" "${cmd[*]}" "$RESET"
    return 1
  fi
}

main() {
  local failed=0

  if [[ "$mode" == "check" ]]; then
    run_step "$RUFF" check "${targets[@]}" || failed=1
    run_step "$RUFF" format --check "${targets[@]}" || failed=1
  else
    run_step "$RUFF" check "${targets[@]}" --fix || failed=1
    run_step "$RUFF" format "${targets[@]}" || failed=1
  fi

  run_step "$MYPY" "${targets[@]}" || failed=1

  printf '%b\n========================================================%b\n' "$CYAN" "$RESET"
  if [[ $failed -eq 0 ]]; then
    printf '%bLinting and type-checking completed successfully.%b\n' "$GREEN" "$RESET"
  else
    printf '%bLinting/type-checking completed with errors. See output above.%b\n' "$RED" "$RESET"
  fi
  printf '%b========================================================%b\n' "$CYAN" "$RESET"

  exit "$failed"
}

main
