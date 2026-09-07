# HYDRA-UMC-VISUAL-SERVOING-API — CLI Reference

`hydra-umc-visual-servoing-api` is a Python console script
(`src/hydra_umc_visual_servoing_api/main.py`, installed as an entry point
via `pyproject.toml`). What's real in v0: the "Error Calculation (Pose
Delta)" step of the servoing loop — a Position-Based Visual Servoing
(PBVS) correction law that turns a current/target 6-DOF pose pair into a
bounded velocity command — plus the real request-vs-authorization safety
gate in front of it. Both are independent of the Hailo-8 hardware that
would supply the pose estimate in a real deployment; 6-DOF pose
*estimation* itself, and the low-latency gRPC feed towards the HYDRA-UMC
core, still need that real hardware and land later. Every example below
was captured from a real run of the installed CLI — not written from
memory.

## Usage

```
$ hydra-umc-visual-servoing-api -h
usage: hydra-umc-visual-servoing-api [-h] {correct,request,serve} ...

positional arguments:
  {correct,request,serve}
    correct             Compute the PBVS pose error and velocity command from
                        current to target pose.
    request             Authorize and (if accepted) compute a correction from
                        a full visual target request - real request-vs-
                        authorization boundary, unlike the lower-level
                        'correct' command which always computes a command from
                        a bare pose pair.
    serve               Run the real PBVS correction law and authorization
                        gate as a JSON/HTTP API (POST /correct, POST /request)
                        - the same functions the 'correct'/'request'
                        subcommands run, reachable from a real caller instead
                        of one-shot CLI args.

options:
  -h, --help            show this help message and exit
```

Bare invocation (no subcommand) prints identity/version/role and exits `0`:

```
$ hydra-umc-visual-servoing-api
HYDRA-UMC-VISUAL-SERVOING-API v0.0.5
Closed-loop kinematic correction from Hailo-8 visual feedback to real-time pose corrections for the HYDRA-UMC core.
```

Every `--current`/`--target` pose is `x,y,z,roll,pitch,yaw` (meters,
radians).

## Commands

### `correct --current POSE --target POSE [--gain G] [--max-linear-speed S] [--max-angular-speed S] [--linear-tol T] [--angular-tol T]`

```
$ hydra-umc-visual-servoing-api correct -h
usage: hydra-umc-visual-servoing-api correct [-h] --current CURRENT
                                             --target TARGET [--gain GAIN]
                                             [--max-linear-speed MAX_LINEAR_SPEED]
                                             [--max-angular-speed MAX_ANGULAR_SPEED]
                                             [--linear-tol LINEAR_TOL]
                                             [--angular-tol ANGULAR_TOL]

options:
  -h, --help            show this help message and exit
  --current CURRENT     x,y,z,roll,pitch,yaw (meters, radians)
  --target TARGET       x,y,z,roll,pitch,yaw (meters, radians)
  --gain GAIN           Proportional control gain (default: 1.0)
  --max-linear-speed MAX_LINEAR_SPEED
                        Clamp the linear velocity command's norm (m/s)
  --max-angular-speed MAX_ANGULAR_SPEED
                        Clamp the angular velocity command's norm (rad/s)
  --linear-tol LINEAR_TOL
                        Linear convergence tolerance in meters (default:
                        0.001)
  --angular-tol ANGULAR_TOL
                        Angular convergence tolerance in radians (default:
                        0.01)
```

The lower-level command: always computes a command from a bare pose
pair, with no authorization gate at all.

```
$ hydra-umc-visual-servoing-api correct --current 0.10,0.20,0.30,0.0,0.0,0.0 --target 0.15,0.20,0.25,0.0,0.0,0.10
pose error   : dx=0.050000 dy=0.000000 dz=-0.050000  droll=0.000000 dpitch=0.000000 dyaw=0.100000
error norm   : linear=0.070711 m  angular=0.100000 rad
velocity cmd : vx=0.050000 vy=0.000000 vz=-0.050000  wroll=0.000000 wpitch=0.000000 wyaw=0.100000
converged    : False
$ echo $?
0
```

With `--gain` and `--max-linear-speed`, the raw proportional command is
scaled by the gain and then clamped to the declared speed limit — here a
0.4 m gain-2.0 error would command 0.8 m/s, but `--max-linear-speed 0.05`
clamps it to exactly `0.05`:

```
$ hydra-umc-visual-servoing-api correct --current 0.10,0.20,0.30,0.0,0.0,0.0 --target 0.50,0.20,0.30,0.0,0.0,0.0 --gain 2.0 --max-linear-speed 0.05
pose error   : dx=0.400000 dy=0.000000 dz=0.000000  droll=0.000000 dpitch=0.000000 dyaw=0.000000
error norm   : linear=0.400000 m  angular=0.000000 rad
velocity cmd : vx=0.050000 vy=0.000000 vz=0.000000  wroll=0.000000 wpitch=0.000000 wyaw=0.000000
converged    : False
$ echo $?
0
```

A real malformed pose (wrong field count), exit code `1`:

```
$ hydra-umc-visual-servoing-api correct --current "0.1,0.2,0.3" --target "0.1,0.2,0.3,0,0,0"
error: expected 6 comma-separated values (x,y,z,roll,pitch,yaw), got 3: '0.1,0.2,0.3'
$ echo $?
1
```

A non-numeric pose value:

```
$ hydra-umc-visual-servoing-api correct --current "a,b,c,0,0,0" --target "0.1,0.2,0.3,0,0,0"
error: non-numeric value in pose 'a,b,c,0,0,0': could not convert string to float: 'a'
$ echo $?
1
```

### `request --current POSE --target POSE --frame-id ID --confidence C --data-age-ms MS --safety-state STATE [--min-confidence C] [--max-data-age-ms MS] [--gain G] [--max-linear-speed S] [--max-angular-speed S]`

```
$ hydra-umc-visual-servoing-api request -h
usage: hydra-umc-visual-servoing-api request [-h] --current CURRENT
                                             --target TARGET
                                             --frame-id FRAME_ID
                                             --confidence CONFIDENCE
                                             --data-age-ms DATA_AGE_MS
                                             --safety-state SAFETY_STATE
                                             [--min-confidence MIN_CONFIDENCE]
                                             [--max-data-age-ms MAX_DATA_AGE_MS]
                                             [--gain GAIN]
                                             [--max-linear-speed MAX_LINEAR_SPEED]
                                             [--max-angular-speed MAX_ANGULAR_SPEED]

options:
  -h, --help            show this help message and exit
  --current CURRENT     x,y,z,roll,pitch,yaw (meters, radians)
  --target TARGET       x,y,z,roll,pitch,yaw (meters, radians)
  --frame-id FRAME_ID   Identifier of the visual frame this target came from
  --confidence CONFIDENCE
                        Detector confidence for this frame, 0.0-1.0
  --data-age-ms DATA_AGE_MS
                        How old the visual estimate is, in milliseconds
  --safety-state SAFETY_STATE
                        Upstream SafetyState (e.g.
                        READY/INHIBITED/FAULT/SAFE_STOP) - only 'READY'
                        authorizes a correction
  --min-confidence MIN_CONFIDENCE
                        Minimum confidence required to trust the frame
                        (default: 0.6)
  --max-data-age-ms MAX_DATA_AGE_MS
                        Maximum age in ms before the frame is considered stale
                        (default: 200.0)
  --gain GAIN           Proportional control gain (default: 1.0)
  --max-linear-speed MAX_LINEAR_SPEED
                        Clamp the linear velocity command's norm (m/s)
  --max-angular-speed MAX_ANGULAR_SPEED
                        Clamp the angular velocity command's norm (rad/s)
```

`request` is the real safety gate in front of `correct`: it only ever
computes a command when the upstream `--safety-state` is `READY` AND the
visual data behind the request is fresh and confident enough. There are
three distinct outcomes, each with its own exit code — `ACCEPTED` (`0`),
`REJECTED` (`1`, a real per-frame trust problem: stale or low-confidence
data, checked *after* the safety state), and `INHIBITED` (`2`, the cell
itself has not confirmed it's safe to move at all — checked first, and
wins even over a perfect frame).

**ACCEPTED** — a fresh, confident frame with the cell `READY`:

```
$ hydra-umc-visual-servoing-api request --current 0.10,0.20,0.30,0.0,0.0,0.0 --target 0.15,0.20,0.25,0.0,0.0,0.10 --frame-id cam0-00123 --confidence 0.92 --data-age-ms 40 --safety-state READY
outcome : ACCEPTED - frame 'cam0-00123' authorized (confidence=0.92, data_age_ms=40.0)
pose error   : dx=0.050000 dy=0.000000 dz=-0.050000  droll=0.000000 dpitch=0.000000 dyaw=0.100000
velocity cmd : vx=0.050000 vy=0.000000 vz=-0.050000  wroll=0.000000 wpitch=0.000000 wyaw=0.100000
$ echo $?
0
```

**REJECTED** — the cell is `READY`, but the visual estimate is stale
(350ms against the default 200ms max):

```
$ hydra-umc-visual-servoing-api request --current 0.10,0.20,0.30,0.0,0.0,0.0 --target 0.15,0.20,0.25,0.0,0.0,0.10 --frame-id cam0-00124 --confidence 0.92 --data-age-ms 350 --safety-state READY
outcome : REJECTED - visual data for frame 'cam0-00124' is 350.0ms old, exceeds the maximum 200.0ms
$ echo $?
1
```

**REJECTED** — fresh data, but confidence too low (0.30 against the
default 0.6 minimum):

```
$ hydra-umc-visual-servoing-api request --current 0.10,0.20,0.30,0.0,0.0,0.0 --target 0.15,0.20,0.25,0.0,0.0,0.10 --frame-id cam0-00125 --confidence 0.30 --data-age-ms 40 --safety-state READY
outcome : REJECTED - confidence 0.3 is below the required minimum 0.6 for frame 'cam0-00125'
$ echo $?
1
```

**INHIBITED** — a perfectly fresh, confident frame, but the cell itself
is not `READY` (checked first, wins regardless of frame quality):

```
$ hydra-umc-visual-servoing-api request --current 0.10,0.20,0.30,0.0,0.0,0.0 --target 0.15,0.20,0.25,0.0,0.0,0.10 --frame-id cam0-00126 --confidence 0.92 --data-age-ms 40 --safety-state FAULT
outcome : INHIBITED - safety_state is 'FAULT', not 'READY'
$ echo $?
2
```

An out-of-range `--confidence` (real request-construction validation,
before authorization even runs):

```
$ hydra-umc-visual-servoing-api request --current 0.1,0.2,0.3,0,0,0 --target 0.1,0.2,0.3,0,0,0 --frame-id x --confidence 1.5 --data-age-ms 10 --safety-state READY
error: confidence must be within [0.0, 1.0], got 1.5
$ echo $?
1
```

## `serve [--addr ADDR] [--port PORT]`

Runs the real PBVS correction law and authorization gate as a plain JSON/
HTTP API (stdlib `http.server`, `ThreadingHTTPServer` - same convention
this family's other `api.py` files already use) instead of one-shot CLI
calls - `POST /correct` and `POST /request` reach the exact same
functions the `correct`/`request` subcommands run above. `ADDR` defaults
to `127.0.0.1`, `PORT` to `8091`.

```
$ hydra-umc-visual-servoing-api serve --addr 127.0.0.1 --port 8091
```

**`GET /stats`** — `{"role": "<the project's own role string>"}`, `200`.

**`POST /correct`** — same fields as the CLI's `correct` flags, as JSON
(`current`/`target` as `"x,y,z,roll,pitch,yaw"` strings, `gain`/
`max_linear_speed`/`max_angular_speed`/`linear_tol`/`angular_tol`
optional):

```bash
$ curl -X POST http://127.0.0.1:8091/correct \
    -d '{"current":"0,0,0,0,0,0","target":"0.1,0,0,0,0,0"}'
{"error": {"dx": 0.1, "dy": 0.0, "dz": 0.0, "droll": 0.0, "dpitch": 0.0, "dyaw": 0.0}, "command": {"vx": 0.1, "vy": 0.0, "vz": 0.0, "wroll": 0.0, "wpitch": 0.0, "wyaw": 0.0}, "converged": false}
```

Always `200` for a real computed command; `400` for a missing/malformed
field (missing `current`/`target`, an unparseable pose string, or an
out-of-range numeric field).

**`POST /request`** — same fields as the CLI's `request` flags, as JSON;
response body is `{"outcome", "reason", "error", "command"}` - `error`/
`command` are `null` when `outcome` isn't `ACCEPTED`. Always `200` for a
well-formed request regardless of outcome (`ACCEPTED`/`REJECTED`/
`INHIBITED` are real, correctly-computed decisions, not server errors -
same reasoning as the CLI's own distinct exit codes below); `400` for a
missing/malformed field.

Any other path, or any non-`GET`/`POST` request, is `404`. There is no
authentication - same as every other loopback-only internal API on the
CM5.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | `correct`: command computed. `request`: outcome `ACCEPTED`. |
| `1` | a malformed pose/request argument (`correct` or `request`), or a `request` outcome of `REJECTED` (the cell is ready, but this specific frame is too stale or not confident enough) |
| `2` | `request` outcome of `INHIBITED` — the upstream `SafetyState` is not `READY`, so no command is computed regardless of frame quality |

## Out of scope for this CLI

Real 6-DOF pose *estimation* from a Hailo-8 visual feed, and the
low-latency gRPC feed of resulting corrections towards the HYDRA-UMC
core, are described in the project README's own roadmap but are not
implemented yet — they need real Hailo-8 hardware this environment does
not have. This CLI only ever consumes pose pairs and requests supplied
directly on the command line.
