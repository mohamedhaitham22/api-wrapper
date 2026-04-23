#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_PATH="${VENV_PATH:-.venv}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8005}"
FORCE_INSTALL_REQUIREMENTS="${FORCE_INSTALL_REQUIREMENTS:-0}"
RELOAD="${RELOAD:-1}"
USE_ROOT="${USE_ROOT:-0}"
SKIP_VENV="${SKIP_VENV:-0}"

PYTHON_BIN=""

if [[ -x "$VENV_PATH/bin/python" ]]; then
  PYTHON_BIN="$VENV_PATH/bin/python"
elif [[ -x "$VENV_PATH/Scripts/python.exe" ]]; then
  PYTHON_BIN="$VENV_PATH/Scripts/python.exe"
elif [[ "$SKIP_VENV" == "1" ]]; then
  echo "Skipping venv creation (SKIP_VENV=1)."
elif [[ ! -d "$VENV_PATH" ]]; then
  echo "Creating virtual environment at '$VENV_PATH'..."
  if python3 -m venv "$VENV_PATH"; then
    if [[ -x "$VENV_PATH/bin/python" ]]; then
      PYTHON_BIN="$VENV_PATH/bin/python"
    elif [[ -x "$VENV_PATH/Scripts/python.exe" ]]; then
      PYTHON_BIN="$VENV_PATH/Scripts/python.exe"
    fi
  else
    echo "Warning: failed to create venv at '$VENV_PATH'. Falling back to current Python environment."
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  else
    echo "Python executable was not found in PATH." >&2
    exit 1
  fi
fi

if [[ ! -f "requirements.txt" ]]; then
  echo "requirements.txt was not found in $SCRIPT_DIR" >&2
  exit 1
fi

if [[ ! -f ".env" ]]; then
  if [[ -f ".env.example" ]]; then
    cp .env.example .env
    echo "Created .env from .env.example"
  else
    echo ".env and .env.example are both missing. Create .env before running." >&2
    exit 1
  fi
fi

get_env_value() {
  local name="$1"
  grep -E "^${name}=" .env | tail -n 1 | cut -d'=' -f2- | sed "s/^['\"]//; s/['\"]$//"
}

MOCKAROO_API_KEY="$(get_env_value MOCKAROO_API_KEY || true)"
MOCKAROO_URL="$(get_env_value MOCKAROO_URL || true)"

if [[ -z "$MOCKAROO_API_KEY" || -z "$MOCKAROO_URL" ]]; then
  echo "MOCKAROO_API_KEY and MOCKAROO_URL must be set in .env" >&2
  exit 1
fi

echo "Using Python: $PYTHON_BIN"

set +e
"$PYTHON_BIN" -c "import fastapi, uvicorn, httpx, dotenv, pydantic, cachetools" >/dev/null 2>&1
DEPS_MISSING=$?
set -e

if [[ "$DEPS_MISSING" != "0" || "$FORCE_INSTALL_REQUIREMENTS" == "1" ]]; then
  echo "Installing dependencies from requirements.txt..."
  "$PYTHON_BIN" -m pip install -r requirements.txt
fi

UVICORN_ARGS=("-m" "uvicorn" "main:app" "--host" "$HOST" "--port" "$PORT")
if [[ "$RELOAD" == "1" ]]; then
  UVICORN_ARGS+=("--reload")
fi

SUDO_CMD=()
if [[ "$USE_ROOT" == "1" && "$EUID" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO_CMD=("sudo" "-E")
  else
    echo "USE_ROOT=1 was requested but sudo is not installed." >&2
    exit 1
  fi
fi

if [[ "$PORT" -lt 1024 && "$EUID" -ne 0 && "$USE_ROOT" != "1" ]]; then
  echo "Port $PORT requires root privileges on Linux." >&2
  echo "Run with USE_ROOT=1 or pick a port >= 1024." >&2
  exit 1
fi

echo "Starting FastAPI app on $HOST:$PORT..."
"${SUDO_CMD[@]}" "$PYTHON_BIN" "${UVICORN_ARGS[@]}"
