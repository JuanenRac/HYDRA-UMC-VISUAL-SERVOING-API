# Changelog

All notable work on **HYDRA-UMC-VISUAL-SERVOING-API** is summarized here,
newest first. Full session-by-session detail (including dates) lives in a
private, unpublished internal log - this file is public, so it
intentionally omits calendar dates.

## Versioning scheme

`pyproject.toml`'s `version` field bumps automatically on every real
build (`build.sh`/`.bat` - see `bump_version.py`, run as the first real
step of both scripts). It follows the ecosystem-wide base-10 "odometer"
rule rather than semantic-versioning judgment calls:

- `PATCH` +1 on every build
- when `PATCH` would exceed 9, it resets to 0 and `MINOR` +1 instead (e.g. `0.0.9` -> `0.1.0`, never `0.0.10`)
- the same carry cascades into `MAJOR` if `MINOR` would exceed 9

---

## [0.0.1] - Initial scaffolding

- **`src/hydra_umc_visual_servoing_api/main.py`** - minimal real entry
  point (prints identity/version/role, exits 0). No servoing logic yet -
  6-DOF pose estimation, Eye-in-Hand/Eye-to-Hand error-delta calculation,
  and the low-latency feed towards the HYDRA-UMC core land in a later
  pass.
- **`pyproject.toml`** - packaging metadata, no runtime dependencies yet.
- **`bump_version.py`** - ecosystem-standard odometer bump script.
- **`build.sh` / `build.bat`**, **`run.sh` / `run.bat`** - venv creation,
  editable install, compile-check, and entry-point execution.
