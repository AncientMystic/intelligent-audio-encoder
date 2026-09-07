#!/usr/bin/env bash
# Quick launcher for intelligent-audio-encoder GUI (Linux/macOS).
# Usage: ./start_gui.sh [--backend cuda --gpu 0]
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
export PYTHONIOENCODING=utf-8
command -v ffmpeg >/dev/null || echo "WARNING: ffmpeg not found. Install it (apt/brew) first."
[ -x tools/bin/exhale/exhale ] && echo "[ok] exhale found" || echo "[!!] exhale missing (Linux: build from https://gitlab.com/ecodis/exhale)"
[ -x tools/bin/fdkaac/fdkaac ] && echo "[ok] fdkaac found" || echo "[!!] fdkaac missing (optional; native aac fallback works)"
if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
command -v "$PY" >/dev/null || { echo "ERROR: python3 not found."; exit 1; }
exec "$PY" -m gui.main_window "$@"
