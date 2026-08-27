#!/usr/bin/env bash
# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - run.sh
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ -f ".venv/Scripts/python.exe" ]; then
  VENV_PY=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
  VENV_PY=".venv/bin/python"
else
  echo "No .venv found - run build.sh first." >&2
  exit 1
fi

exec "$VENV_PY" -m hydra_umc_visual_servoing_api.main "$@"
