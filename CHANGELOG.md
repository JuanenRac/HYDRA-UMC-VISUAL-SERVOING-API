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

## [0.0.3] - Real v0: safety-gated authorization for visual corrections

- **`src/hydra_umc_visual_servoing_api/authorization.py`** (new) - a
  decision layer in front of the existing pure-math correction law:
  `VisualTargetRequest` (current pose, target pose, frame id, detection
  confidence, data age in ms, upstream `safety_state`) and
  `AuthorizationPolicy` (`min_confidence`, `max_data_age_ms`) feed
  `authorize_correction()`, which checks the safety precondition FIRST
  (`safety_state != "READY"` -> `INHIBITED`, wins over everything else,
  reusing the HYDRA-UMC-SDK's `SafetyState` vocabulary as a plain string
  comparison rather than a runtime dependency), then confidence and data
  freshness (`REJECTED` on either), and only computes the real pose error
  and velocity command once all three pass (`ACCEPTED`). Boundary values
  (`confidence == min_confidence`, `data_age_ms == max_data_age_ms`) count
  as valid, matching the inclusive-boundary convention already used in
  HYDRA-UMC-SAFETY-ZONES.
- **`main.py`** - new `request --current X --target X --frame-id ID
  --confidence C --data-age-ms MS --safety-state STATE
  [--min-confidence C] [--max-data-age-ms MS] [--gain G]
  [--max-linear-speed V] [--max-angular-speed W]` subcommand added
  alongside the existing `correct` (left unchanged - `correct` stays the
  low-level pure-math utility; `request` is the safety-gated,
  camera-facing entry point). Exit codes: 0 (`ACCEPTED`), 1 (`REJECTED`),
  2 (`INHIBITED`).
- 20 new tests (`test_authorization.py`) plus 5 new CLI tests for the
  `request` subcommand in `test_cli.py`. 40 tests total.
- Still out of scope, and unchanged from the diagram below: real 6-DOF
  pose *estimation* (needs the Hailo-8 NPU) and the low-latency gRPC
  feed towards the HYDRA-UMC core.

## [0.0.2]

- Build version synchronized with `hydra-umc.project.json` and the repository-native version source.

## [0.0.2] - Real v0: PBVS pose-error and velocity-command correction law

- **`src/hydra_umc_visual_servoing_api/pose.py`** - `Pose6D` (x, y, z,
  roll, pitch, yaw), parsed from a `"x,y,z,roll,pitch,yaw"` string.
- **`src/hydra_umc_visual_servoing_api/servo.py`** - the real
  "Error Calculation (Pose Delta)" step from the servoing loop diagram
  below: `compute_pose_error()` (angular components wrapped to the
  shortest turn, so e.g. 3 rad -> -3 rad is a small step through pi, not
  the long way around), `compute_velocity_command()` (proportional
  control law, with direction-preserving linear/angular speed clamping),
  and `is_converged()`.
- **`main.py`** - new `correct --current X --target X [--gain G]
  [--max-linear-speed V] [--max-angular-speed W] [--linear-tol T]
  [--angular-tol T]` subcommand; prints the pose error, the resulting
  velocity command, and whether the pose has converged.
- 15 tests (`test_pose.py`, `test_servo.py`, `test_cli.py`).
- `pyproject.toml` - added a `dev` extra (`pytest`).
- `build.sh`/`build.bat` - fixed the version-bump step ordering (the
  manifest sync must run after, not before, the odometer bump, or the
  manifest ends up one version behind), added the real test-suite step,
  and the no-autoclose-on-double-click behavior common to the rest of
  the ecosystem's scripts.
- `run.sh`/`run.bat` - now forward CLI arguments through to the entry
  point instead of ignoring them.
- Still out of scope, and unchanged from the diagram below: real 6-DOF
  pose *estimation* (needs the Hailo-8 NPU) and the low-latency gRPC
  feed towards the HYDRA-UMC core.

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
