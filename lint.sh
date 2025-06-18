#!/usr/bin/env bash
# lint.sh – helper script to format, lint, and type-check the codebase in one shot.
#
# It sequentially runs:
#   1. ruff format   – auto-formats the code
#   2. ruff check    – lints (with --fix) to auto-apply safe fixes
#   3. mypy          – static type checking
#
# Each section is clearly delineated and colour-coded. The script exits with
# a non-zero status if *any* step fails.

set -euo pipefail

# If the terminal supports colours, configure them; otherwise fallback to no-op.
if command -v tput >/dev/null 2>&1 && [[ -t 1 ]]; then
  GREEN="$(tput setaf 2)"
  RED="$(tput setaf 1)"
  CYAN="$(tput setaf 6)"
  RESET="$(tput sgr0)"
else
  GREEN=""; RED=""; CYAN=""; RESET=""
fi

run_step() {
  local cmd=("$@")
  echo -e "${CYAN}\n────────────────────────────────────────────────────────\n▶ ${cmd[*]}\n────────────────────────────────────────────────────────${RESET}"
  if "${cmd[@]}"; then
    echo -e "${GREEN}✔ Successfully finished: ${cmd[*]}${RESET}"
  else
    echo -e "${RED}✖ Failed: ${cmd[*]}${RESET}"
    return 1
  fi
}

main() {
  local failed=0

  run_step ruff format .             || failed=1
  run_step ruff check . --fix        || failed=1
  run_step mypy .                    || failed=1

  echo -e "${CYAN}\n========================================================${RESET}"
  if [[ $failed -eq 0 ]]; then
    echo -e "${GREEN}Linting & type-checking completed successfully!${RESET}"
  else
    echo -e "${RED}Linting/type-checking completed with errors. See output above.${RESET}"
  fi
  echo -e "${CYAN}========================================================${RESET}"

  exit $failed
}

main "$@"
