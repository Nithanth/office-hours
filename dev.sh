#!/usr/bin/env bash
# Start Office Hours in development mode.
#
# Usage:
#   ./dev.sh                  # start on PORT from .env, default 8000
#   ./dev.sh --reload-dir docs # pass extra args through to uvicorn
#
# Set OH_DEV_KILL_PORT=1 to terminate an existing listener on PORT before start.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if command -v tput >/dev/null 2>&1 && [[ -t 1 ]]; then
  CYAN="$(tput setaf 6)"
  GREEN="$(tput setaf 2)"
  RED="$(tput setaf 1)"
  RESET="$(tput sgr0)"
else
  CYAN=""
  GREEN=""
  RED=""
  RESET=""
fi

msg() { printf '%b▶ %s%b\n' "$CYAN" "$*" "$RESET"; }
ok() { printf '%b✔ %s%b\n' "$GREEN" "$*" "$RESET"; }
err() { printf '%b✖ %s%b\n' "$RED" "$*" "$RESET" >&2; }

if [[ -f .env && "${OH_SKIP_DOTENV:-0}" != "1" ]]; then
  msg "Loading environment variables from .env"
  preserved_env_file="$(mktemp "${TMPDIR:-/tmp}/oh-env.XXXXXX")"
  while IFS= read -r line; do
    line="${line#"${line%%[![:space:]]*}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    line="${line#export }"
    key="${line%%=*}"
    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && -n "${!key+x}" ]]; then
      printf 'export %s=%q\n' "$key" "${!key}" >> "$preserved_env_file"
    fi
  done < .env

  set -a
  # shellcheck disable=SC1091
  source .env
  set +a

  # Restore variables that were explicitly set by the parent shell. This lets
  # one-off overrides like `PORT=9000 ./dev.sh` win over values in `.env`.
  # shellcheck disable=SC1090
  source "$preserved_env_file"
  rm -f "$preserved_env_file"
fi

export ENV="${ENV:-dev}"
export LOG_LEVEL="${LOG_LEVEL:-info}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ -d .venv ]]; then
    msg "Activating Python virtualenv (.venv)"
    # shellcheck disable=SC1091
    source .venv/bin/activate
  elif [[ -d venv ]]; then
    msg "Activating Python virtualenv (venv)"
    # shellcheck disable=SC1091
    source venv/bin/activate
  fi
fi

PYTHON_BIN="${PYTHON:-python}"
if ! "$PYTHON_BIN" -c "import uvicorn" >/dev/null 2>&1; then
  err "uvicorn is not installed in the active Python environment"
  printf 'Install dependencies with: uv sync\n' >&2
  exit 127
fi

port_pids() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true
  fi
}

pids="$(port_pids)"
if [[ -n "$pids" ]]; then
  if [[ "${OH_DEV_KILL_PORT:-0}" == "1" ]]; then
    msg "Stopping existing listener(s) on port $PORT: $pids"
    kill $pids 2>/dev/null || true
    sleep 1
    remaining="$(port_pids)"
    if [[ -n "$remaining" ]]; then
      msg "Force-stopping listener(s) on port $PORT: $remaining"
      kill -9 $remaining 2>/dev/null || true
    fi
  else
    err "Port $PORT is already in use by PID(s): $pids"
    printf 'Set PORT=another_port or run: OH_DEV_KILL_PORT=1 ./dev.sh\n' >&2
    exit 1
  fi
fi

UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-$LOG_LEVEL}"
UVICORN_CMD=(
  "$PYTHON_BIN" -m uvicorn main:app
  --reload
  --reload-dir src
  --reload-dir static
  --host "$HOST"
  --port "$PORT"
  --log-level "$UVICORN_LOG_LEVEL"
)

ok "PYTHONPATH=$PYTHONPATH"
msg "Starting server: ${UVICORN_CMD[*]} $*"
exec "${UVICORN_CMD[@]}" "$@"
