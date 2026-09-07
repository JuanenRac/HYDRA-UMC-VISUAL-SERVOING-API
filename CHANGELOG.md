# Changelog

All notable work on **HYDRA-UMC-VISUAL-SERVOING-API** is summarized here,
newest first. This file intentionally omits calendar dates from individual
entries.

## Versioning scheme

`pyproject.toml`'s `version` field bumps automatically on every real
build (`build.sh`/`.bat` - see `bump_version.py`, run as the first real
step of both scripts). It follows the ecosystem-wide base-10 "odometer"
rule rather than semantic-versioning judgment calls:

- `PATCH` +1 on every build
- when `PATCH` would exceed 9, it resets to 0 and `MINOR` +1 instead (e.g. `0.0.9` -> `0.1.0`, never `0.0.10`)
- the same carry cascades into `MAJOR` if `MINOR` would exceed 9

---

## [Unreleased] - bounded request bodies, real pytest in CI

- **`api.py`'s `_read_json_body()` now caps request bodies** (`MAX_BODY_BYTES`,
  1 MiB) - found in an ecosystem-wide software-improvements audit: this
  endpoint used to read `Content-Length` bytes with no upper bound before
  parsing, so a malformed or oversized header let a caller force unbounded
  memory buffering. An over-limit request is drained (up to `DRAIN_CAP_BYTES`)
  before the clean 400 is sent, avoiding a real `ConnectionAbortedError` race
  where the client's own send() is still in flight when the handler closes
  the connection - same root cause and same fix already shipped for this
  exact gap in HYDRA-UMC-ANOMALY-DETECTOR. New end-to-end regression test
  against a live server, not just the helper function in isolation.
- **`.github/workflows/ci.yml`** - the real `tests/` pytest suite is now
  actually installed and run in CI, matching the fix already landed in
  sibling repos this session. This repo's own baseline workflow only ever
  compile-checked (`py_compile`) `.py` files - it never ran `pytest`, so a
  regression in `tests/` could be merged without CI ever failing. CI-only
  fix, no runtime code changed, no version bump.

## [0.0.9] - default loopback port moved off a real collision

Found deploying this service alongside `HYDRA-UMC-VOICE-UI` on the same
CM5 for the first time: both hardcoded the exact same default loopback
port (`8091`), so whichever service started second crash-looped forever
with "Address already in use" - a real conflict, not a hypothetical one.
Moved this API's own default (CLI flag, systemd unit, Dockerfile, CLI
reference) to `8116`, the next free slot after this ecosystem's own
8090-8115 loopback API range. `HYDRA-UMC-VOICE-UI` keeps `8091`, which is
the one referenced elsewhere across the ecosystem's own deployment docs.

## [0.0.8] - A non-object JSON body and extreme-but-finite poses no longer crash the handler (SERVO-01/SERVO-02)

Found in an ecosystem-wide software-improvements audit, both P1:

- **SERVO-01.** A syntactically valid JSON body whose top level wasn't
  an object - `[]`, `null`, a bare string, a bare number - parsed
  successfully (so the existing `except (json.JSONDecodeError,
  ValueError)` never caught it), then crashed the handler thread with
  an uncaught `TypeError` from `body["current"]` - no HTTP response
  reached the client at all. `do_POST` now checks `isinstance(body,
  dict)` once, centrally, right after parsing, and returns a real 400
  instead.
- **SERVO-02.** `_clamp_vector`'s norm was `sqrt(sum(c**2))` - squaring
  a perfectly finite but extreme component (~1e200) overflows a float,
  and Python raises `OverflowError` for that specific case (unlike most
  float arithmetic, which silently returns `inf`). Switched to
  `math.hypot`, which computes the same Euclidean norm without that
  intermediate overflow - applied to `PoseError.linear_norm`/
  `angular_norm` too. That alone wasn't the whole fix: subtracting two
  individually-valid, extreme poses can still silently overflow to
  `inf` (`compute_pose_error` now rejects a non-finite result
  immediately, protecting every consumer of `PoseError`), and gain
  applied to an extreme delta - or an `inf * 0.0` from a subsequent
  clamp scale - can still produce `inf`/`NaN` even when clamping is
  requested, or reach the client untouched when no `max_*_speed` was
  given at all (`_clamp_vector` used to short-circuit without checking
  anything in that case). `compute_velocity_command` now explicitly
  rejects a non-finite linear or angular result before returning it -
  the real, final safety net a 200 response with a `NaN`/`inf` velocity
  command would otherwise have slipped past.
- 9 new tests across `test_servo.py` (the exact overflow-to-infinite
  subtraction, hypot not raising `OverflowError` for an extreme finite
  component, both the no-clamp and clamp-produces-NaN cases) and
  `test_api.py` (all 4 non-object JSON bodies over a real running
  server, plus confirming the server still serves a valid request
  afterward).

Verified with `pytest tests/` (79/79 PASS, this repo's own real test
command).

## [0.0.7] - finite, directional servo safety bounds + a real crash fixed in POST /request

- **`pose.py` / `servo.py`** - parsed poses reject `NaN` and infinity; gain,
  speed limits and convergence tolerances now require finite positive values.
  A negative speed limit can no longer invert a correction vector and a
  non-finite gain cannot create an unsafe velocity command.
- **Real bug fixed in `api.py`**: `POST /request` parsed `gain`,
  `max_linear_speed` and `max_angular_speed` from the request body
  *after* the try/except that turns a malformed field into a clean `400`
  - unlike `POST /correct`, which already did this correctly. A
  non-numeric `gain` (e.g. `"gain": "fast"`) raised an uncaught
  `ValueError` in the handler thread, closing the connection with no
  HTTP response at all instead of a `400`. Confirmed live against a
  real running server before and after the fix. Fixed by moving the
  parse/call inside the existing try/except, matching `_handle_correct`'s
  shape; new regression test in `tests/test_api.py`.
- Added regression tests for non-finite poses, gains and bounds.

## [0.0.6]

- **New real `Dockerfile`**, closing the real gap HYDRA-UMC-VISION-NODE's
  own `docker-compose.yml` named ("still skeleton-stage... no Dockerfile
  of their own"). Same `--addr`/`--port` CLI the real CM5 systemd unit
  (`systemd/hydra-umc-visual-servoing-api.service`) already runs, bound
  to `0.0.0.0` instead of `127.0.0.1` (the container's own network
  namespace is the real isolation boundary here, not the loopback
  bind), non-root. Not build-tested (no Docker runtime on this dev
  machine) - every path/flag matches the one already verified live on
  the real CM5.

## [0.0.5] - Real v0: JSON/HTTP server mode, plus CM5 deployment

- **`api.py`** (new) - `POST /correct` and `POST /request` reach the
  exact same `servo.py`/`authorization.py` functions the CLI's
  `correct`/`request` subcommands already run, over a real stdlib
  `ThreadingHTTPServer`, reusing `Pose6D.parse`'s exact
  "x,y,z,roll,pitch,yaw" string format rather than inventing a second
  pose encoding to keep in sync. `/request`'s `INHIBITED`/`REJECTED`/
  `ACCEPTED` outcomes are all real, correctly-computed decisions, not
  server errors - the route answers 200 for all three; 400 is reserved
  for a genuinely malformed request body. Real gap this closes: this
  project's own logic was only ever reachable as a one-shot CLI, with no
  way for HYDRA-UMC-SERVER or any other real caller to reach it.
- **`main.py`** - new `serve` subcommand (`--addr`/`--port`, default
  `127.0.0.1:8091`).
- **`systemd/hydra-umc-visual-servoing-api.service`** (new) - loopback-
  only unit for `HYDRA-UMC-OS/provisioning/install_visual_servoing_api.sh`
  (new, that repo), same stdlib "copy src/ + PYTHONPATH" shape as
  `install_datalake.sh` - this project is genuinely stdlib-only Python.
- 13 new tests (`tests/test_api.py`, real end-to-end HTTP against a real
  socket) - 68 total.

## [0.0.4] - Real HailoRT integration boundary, prepared ahead of the Hailo-8 module

- **Added `src/hydra_umc_visual_servoing_api/hailo_runtime.py`** (new) -
  a real HailoRT (`hailo_platform`) integration boundary, so this API is
  ready to actually consume a real Hailo-8 pose estimate the moment a
  real module is attached, rather than starting that work from zero
  then. `open_vdevice()` and `load_hailo_pose_model()` are written
  against the real, confirmed HailoRT Python API (`VDevice`, `HEF`,
  `ConfigureParams.create_from_hef(..., interface=HailoStreamInterface.PCIe)`),
  lazily imported (same pattern as this ecosystem's other real hardware
  transports) so this development machine, which has no `hailort`
  installed, degrades to a clear `HailoNotAvailableError` instead of a
  bare `ImportError`. `hailo_output_to_pose()` adapts a real
  `InferVStreams` inference result into this project's own `Pose6D` -
  the exact shape `servo.py`'s `compute_pose_error()` already consumes -
  and needs no real hailort to exercise, fully unit-tested against
  plain-list fakes standing in for a real numpy-backed result. `hailort`
  added as a new `[project.optional-dependencies]` extra (`pip install
  .[hailo]`); never required. 7 new tests (57 total). Actually running
  inference still needs a real compiled pose-estimation `.hef` and a
  physical Hailo-8 module, both future work - but consuming one, once it
  exists, is no longer unwritten code.

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
