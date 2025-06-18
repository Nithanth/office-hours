#!/usr/bin/env bash
# dev.sh – convenience script to start Lux Scholar in development mode.
#
# Features:
#   • Loads .env if present (with `export`-compatible lines).
#   • Activates local `.venv` (if it exists and no venv active).
#   • Starts the FastAPI server with uvicorn & auto-reload.
#   • Gracefully handles Ctrl-C.
#
# Usage: ./dev.sh [uvicorn-extra-args]
# Any extra arguments are passed directly to uvicorn.

set -euo pipefail

###################################
# Colour helper
###################################
if command -v tput >/dev/null 2>&1 && [[ -t 1 ]]; then
  CYAN="$(tput setaf 6)"; GREEN="$(tput setaf 2)"; RESET="$(tput sgr0)"
else
  CYAN=""; GREEN=""; RESET=""
fi

msg() { echo -e "${CYAN}▶ $*${RESET}"; }

###################################
# Load environment variables
###################################
if [[ -f .env ]]; then
  msg "Loading environment variables from .env"
  # shellcheck disable=SC1090
  set -a; source .env; set +a
fi

###################################
# Activate virtual environment
###################################
if [[ -z "${VIRTUAL_ENV:-}" ]] && [[ -d .venv ]]; then
  msg "Activating Python virtualenv (.venv)"
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

###################################
# Start the server
###################################
UVICORN_CMD=(uvicorn app:app --reload --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" "$@")
msg "Starting server: ${UVICORN_CMD[*]}"
exec "${UVICORN_CMD[@]}"
