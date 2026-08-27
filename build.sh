#!/usr/bin/env bash
# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - build.sh
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
set -euo pipefail
python3 "$(dirname "$0")/bump_manifest_version.py" || exit 1
cd "$(dirname "$0")"

echo "== HYDRA-UMC-VISUAL-SERVOING-API :: build =="

echo "-- Odometer version bump --"
python3 bump_version.py 2>/dev/null || python bump_version.py

echo "-- Creating/using virtual environment (.venv) --"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
fi

if [ -f ".venv/Scripts/python.exe" ]; then
  VENV_PY=".venv/Scripts/python.exe"   # Windows venv layout
else
  VENV_PY=".venv/bin/python"           # POSIX venv layout
fi

echo "-- Installing project + dependencies (editable) --"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -e .

echo "-- Compile-check every source file (python -m compileall) --"
"$VENV_PY" -m compileall -q src

echo "== Build OK =="
