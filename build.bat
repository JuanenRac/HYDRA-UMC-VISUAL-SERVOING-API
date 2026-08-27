@echo off
REM =============================================================================
REM HYDRA-UMC-VISUAL-SERVOING-API - build.bat
REM Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
REM GPL-3.0 - see LICENSE
REM =============================================================================
setlocal
cd /d "%~dp0"

echo == HYDRA-UMC-VISUAL-SERVOING-API :: build ==

echo -- Odometer version bump --
python bump_version.py
if errorlevel 1 ( echo NATIVE VERSION BUMP FAILED. & pause & exit /b 1 )
python "%~dp0bump_manifest_version.py" --sync
if errorlevel 1 ( echo VERSION SYNCHRONIZATION FAILED. & pause & exit /b 1 )
if errorlevel 1 goto :error

echo -- Creating/using virtual environment (.venv) --
if not exist ".venv" (
    python -m venv .venv
    if errorlevel 1 goto :error
)

set VENV_PY=.venv\Scripts\python.exe

echo -- Installing project + dependencies (editable) --
"%VENV_PY%" -m pip install --quiet --upgrade pip
if errorlevel 1 goto :error
"%VENV_PY%" -m pip install --quiet -e .
if errorlevel 1 goto :error

echo -- Compile-check every source file (python -m compileall) --
"%VENV_PY%" -m compileall -q src
if errorlevel 1 goto :error

echo == Build OK ==
exit /b 0

:error
echo == Build FAILED ==
exit /b 1
