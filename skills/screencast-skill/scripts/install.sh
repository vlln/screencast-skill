#!/usr/bin/env bash
# One-command setup for a fresh machine. No compiler, no Xcode Command Line Tools.
#
#   scripts/install.sh            # install (no-op if it is already installed)
#   scripts/install.sh --check    # only report state, change nothing
#
# Safe to run on every session: a second run reuses the existing .venv and does
# no network work. A new agent session has no memory of the first, so the script
# -- not the caller -- decides whether there is anything to do.
set -euo pipefail
cd "$(dirname "$0")"

CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

PYTHON_SPEC="${REC_PYTHON:-3.12}"

case "$(uname -s)" in
  Darwin)
    DEPS="pyobjc-framework-Quartz numpy Pillow"
    VENV_PY=".venv/bin/python"
    IMPORTS="import Quartz, numpy, PIL" ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    DEPS="numpy Pillow pynput"
    VENV_PY=".venv/Scripts/python.exe"
    IMPORTS="import numpy, PIL, pynput" ;;
  *)
    DEPS="numpy Pillow pynput"
    VENV_PY=".venv/bin/python"
    IMPORTS="import numpy, PIL, pynput" ;;
esac

# ---- is it already there? ------------------------------------------------
READY=0
if [ -x "$VENV_PY" ] && "$VENV_PY" -c "$IMPORTS" >/dev/null 2>&1; then
  READY=1
fi

# The cursor artwork is generated from THIS machine's system files, so it is
# never committed -- a fresh install has to produce it once.
ensure_cursors() {
  local meta="assets/cursors/meta.json"
  if [ -f "$meta" ] && [ "$(ls assets/cursors/*.png 2>/dev/null | wc -l | tr -d ' ')" -ge 8 ]; then
    return
  fi
  echo "-- exporting this machine's cursor set (generated locally, never committed)"
  "$VENV_PY" rec cursors >/dev/null || true
}

if [ "$READY" = 1 ]; then
  ensure_cursors
  echo "== screencast-skill: already installed, nothing to do =="
  if [ "$CHECK_ONLY" = 1 ]; then
    "$VENV_PY" rec doctor || true
    exit 0
  fi
  "$VENV_PY" rec doctor || true
  exit 0
fi

if [ "$CHECK_ONLY" = 1 ]; then
  echo "== screencast-skill: not installed yet =="
  [ -x "$VENV_PY" ] || echo "   no .venv at scripts/.venv"
  [ -x "$VENV_PY" ] && echo "   .venv exists but dependencies are incomplete"
  echo "   run: scripts/install.sh"
  exit 1
fi

echo "== screencast-skill setup =="

if ! command -v uv >/dev/null 2>&1; then
  cat <<'MSG'
uv is not installed. It is the fastest way to get a correct Python here.

  brew install uv
  # or, without homebrew:
  curl -LsSf https://astral.sh/uv/install.sh | sh

Then re-run scripts/install.sh
MSG
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  case "$(uname -s)" in
    Darwin) echo "ffmpeg is missing — install it first:  brew install ffmpeg" ;;
    *)      echo "ffmpeg is missing — install it first:  winget install Gyan.FFmpeg" ;;
  esac
  exit 1
fi

if [ -x "$VENV_PY" ]; then
  echo "-- reusing existing .venv"
else
  echo "-- creating .venv with Python ${PYTHON_SPEC}"
  uv venv .venv --python "$PYTHON_SPEC"
fi

echo "-- installing dependencies: ${DEPS}"
uv pip install --python "$VENV_PY" $DEPS

ensure_cursors

echo
echo "-- verifying"
"$VENV_PY" rec doctor || true

cat <<'MSG'

Next: grant the two macOS permissions if doctor said DENIED
  1. System Settings > Privacy & Security > Screen Recording   -> add your terminal
  2. System Settings > Privacy & Security > Accessibility      -> add your terminal
Then run:  scripts/.venv/bin/rec doctor
Re-running this script is harmless: it will report "already installed" and stop.
MSG
