#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT/apps/backend"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "[dev_backend] no python interpreter found"
  exit 1
fi

if [ ! -d .venv ] || ! .venv/bin/python -c "import sys" >/dev/null 2>&1 || ! .venv/bin/pip --version >/dev/null 2>&1; then
  rm -rf .venv
  "$PYTHON_BIN" -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q --upgrade pip
pip install -q -r requirements.txt

# load .env from repo root if present
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

case "${USE_AI_STUBS:-true}" in
  0|false|False|FALSE|no|No|NO)
    AI_MODE="real"
    ;;
  *)
    AI_MODE="stub"
    ;;
esac

echo "[dev_backend] ai mode: $AI_MODE"

if [ "$AI_MODE" = "real" ]; then
  if [ -z "${DASHSCOPE_API_KEY:-}" ] && [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "[dev_backend] USE_AI_STUBS=false but neither DASHSCOPE_API_KEY nor GEMINI_API_KEY is set"
    echo "[dev_backend] set DASHSCOPE_API_KEY for Qwen/HappyHorse, or flip USE_AI_STUBS=true for local stub mode"
    exit 1
  fi

  if [ -z "${ELEVENLABS_API_KEY:-}" ]; then
    echo "[dev_backend] ELEVENLABS_API_KEY is not set; narration requests will fail until you add it"
  fi
else
  if [ -n "${DASHSCOPE_API_KEY:-}" ] || [ -n "${GEMINI_API_KEY:-}" ] || [ -n "${ELEVENLABS_API_KEY:-}" ]; then
    echo "[dev_backend] provider keys are present, but USE_AI_STUBS is still on so the backend will use stub providers"
  fi
fi

# backend imports both `app.*` (from apps/backend) and `ai.*` (from apps)
export PYTHONPATH="$REPO_ROOT/apps:$REPO_ROOT/apps/backend${PYTHONPATH:+:$PYTHONPATH}"

exec uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
