# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""Entry point for HYDRA-UMC-VISUAL-SERVOING-API.

Real v0: the "Error Calculation (Pose Delta)" step of the servoing loop
(pose.py + servo.py) - a Position-Based Visual Servoing correction law
that turns a current/target 6-DOF pose pair into a bounded velocity
command, independent of the Hailo-8 hardware that would supply the pose
estimate in a real deployment. 6-DOF pose *estimation* itself, and the
low-latency gRPC feed towards the HYDRA-UMC core, still need that real
hardware and land later.
"""
from __future__ import annotations

from typing import Any

import argparse
import json
import os
import sys
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version

from .access import bind_problem, load_token
from .api import VisualServoingServer
from .health_route import health_open_requested
from .bench import BenchError, load_bench_config, run_bench, sweep_gains
from .authorization import (
    AuthorizationPolicy,
    RequestOutcome,
    VisualTargetRequest,
    authorize_correction,
)
from .limits import (
    DEFAULT_MAX_ANGULAR_SPEED,
    DEFAULT_MAX_DATA_AGE_MS,
    DEFAULT_MAX_GAIN,
    DEFAULT_MAX_LINEAR_SPEED,
    DEFAULT_MIN_CONFIDENCE,
    DEFAULT_MAX_SAFETY_AGE_MS,
    DEFAULT_VALID_FOR_MS,
    ServoLimits,
)
from .ibvs import IbvsError, load_ibvs_policy
from .servo_log import ServoLogError, load_servo_log_policy
from .orientation import OrientationConfigError, load_orientation_policy, set_active_policy
from .pose import Pose6D, PoseParseError
from .safety_source import DEFAULT_TIMEOUT_MS, SafetyZonesClient
from .state_verification import StateVerificationError, StateVerifier, load_state_verification_policy
from .servo import compute_pose_error, compute_velocity_command, is_converged

PROJECT_NAME = "HYDRA-UMC-VISUAL-SERVOING-API"
DIST_NAME = "hydra-umc-visual-servoing-api"
ROLE = (
    "Closed-loop kinematic correction from Hailo-8 visual feedback to "
    "real-time pose corrections for the HYDRA-UMC core."
)


def get_version() -> str:
    """Read the running version from installed package metadata, which is
    sourced from pyproject.toml - the single place bump_version.py edits."""
    try:
        return version(DIST_NAME)
    except PackageNotFoundError:
        return "0.0.0-dev (package not installed - run build.sh/build.bat first)"


def _apply_config(args: argparse.Namespace) -> bool:
    """Read the orientation settings of the configuration file; False (after the message) when they cannot be used."""
    try:
        set_active_policy(load_orientation_policy(args.config))
    except OrientationConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return False
    return True


def _cmd_correct(args: argparse.Namespace) -> int:
    if not _apply_config(args):
        return 2
    try:
        current = Pose6D.parse(args.current)
        target = Pose6D.parse(args.target)
    except PoseParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        error = compute_pose_error(current, target)
        command = compute_velocity_command(
            error, gain=args.gain,
            max_linear_speed=args.max_linear_speed,
            max_angular_speed=args.max_angular_speed,
        )
        converged = is_converged(error, args.linear_tol, args.angular_tol)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"pose error   : dx={error.dx:.6f} dy={error.dy:.6f} dz={error.dz:.6f}  "
          f"droll={error.droll:.6f} dpitch={error.dpitch:.6f} dyaw={error.dyaw:.6f}")
    print(f"error norm   : linear={error.linear_norm:.6f} m  angular={error.angular_norm:.6f} rad")
    print(f"velocity cmd : vx={command.vx:.6f} vy={command.vy:.6f} vz={command.vz:.6f}  "
          f"wroll={command.wroll:.6f} wpitch={command.wpitch:.6f} wyaw={command.wyaw:.6f}")
    print(f"converged    : {converged}")
    return 0


def _cmd_request(args: argparse.Namespace) -> int:
    if not _apply_config(args):
        return 2
    try:
        current = Pose6D.parse(args.current)
        target = Pose6D.parse(args.target)
    except PoseParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        request = VisualTargetRequest(
            current=current,
            target=target,
            frame_id=args.frame_id,
            confidence=args.confidence,
            data_age_ms=args.data_age_ms,
            safety_state=args.safety_state,
        )
        policy = AuthorizationPolicy(
            min_confidence=args.min_confidence,
            max_data_age_ms=args.max_data_age_ms,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        decision = authorize_correction(
            request, policy, gain=args.gain,
            max_linear_speed=args.max_linear_speed,
            max_angular_speed=args.max_angular_speed,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"outcome : {decision.outcome.value.upper()} - {decision.reason}")

    if decision.outcome is not RequestOutcome.ACCEPTED:
        return 2 if decision.outcome is RequestOutcome.INHIBITED else 1

    error: Any = decision.error
    command: Any = decision.command
    print(f"pose error   : dx={error.dx:.6f} dy={error.dy:.6f} dz={error.dz:.6f}  "
          f"droll={error.droll:.6f} dpitch={error.dpitch:.6f} dyaw={error.dyaw:.6f}")
    print(f"velocity cmd : vx={command.vx:.6f} vy={command.vy:.6f} vz={command.vz:.6f}  "
          f"wroll={command.wroll:.6f} wpitch={command.wpitch:.6f} wyaw={command.wyaw:.6f}")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    """Close the servoing loop against a simulated tool (bench.py) and print the result as JSON. Exit 0 when the run (or every run of the sweep that was
    not refused) meets the limits of the file, 1 when one does not, 2 for a bad file."""
    if not _apply_config(args):
        return 2
    try:
        config = load_bench_config(args.bench_config)
        if args.sweep:
            result = sweep_gains(config)
            ok = any(run["ok"] for run in result["runs"])
        else:
            result = run_bench(config)
            ok = bool(result["ok"])
            if not args.trace:
                result.pop("trace")
    except BenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if ok else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    if not _apply_config(args):
        return 2
    try:
        limits = ServoLimits(
            min_confidence=args.min_confidence,
            max_data_age_ms=args.max_data_age_ms,
            max_calibration_age_days=args.max_calibration_age_days,
            max_gain=args.max_gain,
            max_linear_speed=args.max_linear_speed,
            max_angular_speed=args.max_angular_speed,
            valid_for_ms=args.valid_for_ms,
            max_safety_age_ms=args.max_safety_age_ms,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        token = load_token(args.token_file)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    problem = bind_problem(args.addr, token)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    try:
        ibvs_policy = load_ibvs_policy(args.config)
        log_policy = load_servo_log_policy(args.config)
    except (IbvsError, ServoLogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    zones_url = args.safety_zones_url or os.environ.get("HYDRA_UMC_SAFETY_ZONES_URL", "")
    zones_client = None
    if zones_url.strip():
        try:
            zones_token = os.environ.get("HYDRA_UMC_SAFETY_ZONES_TOKEN", "").strip()
            if args.safety_zones_token_file:
                zones_token = Path(args.safety_zones_token_file).read_text(encoding="utf-8").strip()
            zones_client = SafetyZonesClient(zones_url, zones_token, args.safety_zones_timeout_ms)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    verifier = None
    try:
        verification = load_state_verification_policy(args.config)
        if verification.enabled:
            if zones_client is None:
                raise StateVerificationError("safety_state_verification is enabled but there is no SAFETY-ZONES address (--safety-zones-url)")
            verifier = StateVerifier(verification)
    except StateVerificationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    server = VisualServoingServer(
        (args.addr, args.port), ROLE, limits, token, accept_legacy_safety_string=args.accept_legacy_safety_string,
        safety_zones=zones_client, ibvs=ibvs_policy, servo_log=log_policy, state_verifier=verifier,
        require_contract=args.require_contract,
    )
    if args.require_contract:
        print("[visual-servoing-api] only requests that declare schema_version and follow the VisualTargetRequest contract are taken")
    if verifier is not None:
        print(f"[visual-servoing-api] the verdict of SAFETY-ZONES must be signed ({verifier.policy.alg}); anything unsigned, forged, old or replayed is INHIBITED")
    if zones_client is not None:
        print(f"[visual-servoing-api] the safety state is asked of SAFETY-ZONES at {zones_client.base_url}; what a caller says about it is ignored, and a request needs captured_at_utc")
    print(f"[visual-servoing-api] HTTP API listening on {args.addr}:{args.port} ({'Bearer token required' if token else 'no token, loopback only'})")
    print(
        f"[visual-servoing-api] limits fixed at start-up (a request can only tighten them): "
        f"min_confidence={limits.min_confidence} max_data_age_ms={limits.max_data_age_ms} "
        f"max_gain={limits.max_gain} max_linear_speed={limits.max_linear_speed} m/s "
        f"max_angular_speed={limits.max_angular_speed} rad/s valid_for_ms={limits.valid_for_ms}"
    )
    print("[visual-servoing-api] POST /correct, POST /request, GET /stats")
    try:
        server.health_open = health_open_requested(args.health_open)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[visual-servoing-api] shutting down")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydra-umc-visual-servoing-api")
    subparsers = parser.add_subparsers(dest="command")

    correct = subparsers.add_parser(
        "correct",
        help="Compute the PBVS pose error and velocity command from current to target pose.",
    )
    correct.add_argument("--current", required=True, help="x,y,z,roll,pitch,yaw (meters, radians)")
    correct.add_argument("--target", required=True, help="x,y,z,roll,pitch,yaw (meters, radians)")
    correct.add_argument("--gain", type=float, default=1.0, help="Proportional control gain (default: 1.0)")
    correct.add_argument("--max-linear-speed", type=float, default=DEFAULT_MAX_LINEAR_SPEED, dest="max_linear_speed",
                          help=f"Clamp the linear velocity command's norm in m/s (default: {DEFAULT_MAX_LINEAR_SPEED})")
    correct.add_argument("--max-angular-speed", type=float, default=DEFAULT_MAX_ANGULAR_SPEED, dest="max_angular_speed",
                          help=f"Clamp the angular velocity command's norm in rad/s (default: {DEFAULT_MAX_ANGULAR_SPEED})")
    correct.add_argument("--linear-tol", type=float, default=0.001, dest="linear_tol",
                          help="Linear convergence tolerance in meters (default: 0.001)")
    correct.add_argument("--angular-tol", type=float, default=0.01, dest="angular_tol",
                          help="Angular convergence tolerance in radians (default: 0.01)")
    correct.add_argument("--config", default=None, help="Configuration file with how the orientation error is computed (default: the file named by HYDRA_UMC_VISUAL_SERVOING_CONFIG, else quaternion error in the base frame)")
    correct.set_defaults(func=_cmd_correct)

    request = subparsers.add_parser(
        "request",
        help="Authorize and (if accepted) compute a correction from a full visual target "
             "request - real request-vs-authorization boundary, unlike the lower-level "
             "'correct' command which always computes a command from a bare pose pair.",
    )
    request.add_argument("--current", required=True, help="x,y,z,roll,pitch,yaw (meters, radians)")
    request.add_argument("--target", required=True, help="x,y,z,roll,pitch,yaw (meters, radians)")
    request.add_argument("--frame-id", required=True, dest="frame_id",
                          help="Identifier of the visual frame this target came from")
    request.add_argument("--confidence", type=float, required=True,
                          help="Detector confidence for this frame, 0.0-1.0")
    request.add_argument("--data-age-ms", type=float, required=True, dest="data_age_ms",
                          help="How old the visual estimate is, in milliseconds")
    request.add_argument("--safety-state", required=True, dest="safety_state",
                          help="Upstream SafetyState (e.g. READY/INHIBITED/FAULT/SAFE_STOP) - "
                               "only 'READY' authorizes a correction")
    request.add_argument("--min-confidence", type=float, default=0.6, dest="min_confidence",
                          help="Minimum confidence required to trust the frame (default: 0.6)")
    request.add_argument("--max-data-age-ms", type=float, default=200.0, dest="max_data_age_ms",
                          help="Maximum age in ms before the frame is considered stale (default: 200.0)")
    request.add_argument("--gain", type=float, default=1.0, help="Proportional control gain (default: 1.0)")
    request.add_argument("--max-linear-speed", type=float, default=DEFAULT_MAX_LINEAR_SPEED, dest="max_linear_speed",
                          help=f"Clamp the linear velocity command's norm in m/s (default: {DEFAULT_MAX_LINEAR_SPEED})")
    request.add_argument("--max-angular-speed", type=float, default=DEFAULT_MAX_ANGULAR_SPEED, dest="max_angular_speed",
                          help=f"Clamp the angular velocity command's norm in rad/s (default: {DEFAULT_MAX_ANGULAR_SPEED})")
    request.add_argument("--config", default=None, help="Configuration file with how the orientation error is computed (default: the file named by HYDRA_UMC_VISUAL_SERVOING_CONFIG, else quaternion error in the base frame)")
    request.set_defaults(func=_cmd_request)

    serve = subparsers.add_parser(
        "serve",
        help="Run the real PBVS correction law and authorization gate as a JSON/HTTP API "
             "(POST /correct, POST /request) - the same functions the 'correct'/'request' "
             "subcommands run, reachable from a real caller instead of one-shot CLI args.",
    )
    serve.add_argument("--addr", default="127.0.0.1", help="address to bind the HTTP API to")
    serve.add_argument("--health-open", action="store_true", help="answer GET /health (open to loopback, to other callers only with the bearer token); closed by default, or set HYDRA_UMC_VISUAL_SERVOING_API_HEALTH_OPEN=1")
    serve.add_argument("--port", type=int, default=8116, help="port for the HTTP API")
    serve.add_argument("--token-file", default=None, help="file with the bearer token every request must present (or the HYDRA_UMC_VISUAL_SERVOING_TOKEN environment variable). Needed to bind an address that is not loopback.")
    serve.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE, dest="min_confidence",
                       help=f"Lowest detector confidence a request may be accepted with (default: {DEFAULT_MIN_CONFIDENCE})")
    serve.add_argument("--max-data-age-ms", type=float, default=DEFAULT_MAX_DATA_AGE_MS, dest="max_data_age_ms",
                       help=f"Oldest visual data a request may be accepted with, in ms (default: {DEFAULT_MAX_DATA_AGE_MS})")
    serve.add_argument("--max-calibration-age-days", type=float, default=None, dest="max_calibration_age_days",
                       help="Oldest calibration a request may be accepted with (default: not required)")
    serve.add_argument("--max-gain", type=float, default=DEFAULT_MAX_GAIN, dest="max_gain",
                       help=f"Highest control gain a request may ask for (default: {DEFAULT_MAX_GAIN})")
    serve.add_argument("--max-linear-speed", type=float, default=DEFAULT_MAX_LINEAR_SPEED, dest="max_linear_speed",
                       help=f"Highest linear speed any command may have, m/s (default: {DEFAULT_MAX_LINEAR_SPEED})")
    serve.add_argument("--max-angular-speed", type=float, default=DEFAULT_MAX_ANGULAR_SPEED, dest="max_angular_speed",
                       help=f"Highest angular speed any command may have, rad/s (default: {DEFAULT_MAX_ANGULAR_SPEED})")
    serve.add_argument("--valid-for-ms", type=float, default=DEFAULT_VALID_FOR_MS, dest="valid_for_ms",
                       help=f"How long a consumer may keep applying a command, ms (default: {DEFAULT_VALID_FOR_MS})")
    serve.add_argument("--max-safety-age-ms", type=float, default=DEFAULT_MAX_SAFETY_AGE_MS, dest="max_safety_age_ms",
                       help=f"Oldest SafetyState (by its timestamp_utc) a correction may rely on, ms (default: {DEFAULT_MAX_SAFETY_AGE_MS})")
    serve.add_argument("--accept-legacy-safety-string", action="store_true", dest="accept_legacy_safety_string",
                       help="believe a bare safety_state string such as \"READY\" (no time) as before; off by default, when only the SafetyState object with a recent timestamp_utc can authorize a correction")
    serve.add_argument("--safety-zones-url", default=None, dest="safety_zones_url",
                       help="address of HYDRA-UMC-SAFETY-ZONES (or HYDRA_UMC_SAFETY_ZONES_URL): the service then asks it for the latest verdict (GET /state) and ignores the safety_state a caller sends; no answer, an old one or an error is INHIBITED. Off by default, when the caller sends the SafetyState object")
    serve.add_argument("--safety-zones-token-file", default=None, dest="safety_zones_token_file",
                       help="file with the bearer token of SAFETY-ZONES (or HYDRA_UMC_SAFETY_ZONES_TOKEN)")
    serve.add_argument("--safety-zones-timeout-ms", type=float, default=DEFAULT_TIMEOUT_MS, dest="safety_zones_timeout_ms",
                       help=f"how long to wait for SAFETY-ZONES, ms (default: {DEFAULT_TIMEOUT_MS:g})")
    serve.add_argument("--config", default=None, help="Configuration file with how the orientation error is computed (default: the file named by HYDRA_UMC_VISUAL_SERVOING_CONFIG, else quaternion error in the base frame)")
    serve.add_argument("--require-contract", action="store_true", dest="require_contract",
                       help="only take requests that declare schema_version and follow the VisualTargetRequest contract of the SDK (off by default: a request without schema_version is handled as before)")
    serve.set_defaults(func=_cmd_serve)

    bench = subparsers.add_parser(
        "bench",
        help="Close the servoing loop against a simulated tool (delay, lag, noise, missed detections) to try gains before a real robot; every number comes from the file.",
    )
    bench.add_argument("--bench-config", required=True, dest="bench_config", help="JSON file with the session, the plant, the scenario, the limits and (optional) the gains to sweep")
    bench.add_argument("--sweep", action="store_true", help="Run once for every gain of sweep.gains and report each run (without the trace)")
    bench.add_argument("--trace", action="store_true", help="Include the error over time (at most 600 points) in the single run")
    bench.add_argument("--config", default=None, help="Configuration file with how the orientation error is computed (default: the file named by HYDRA_UMC_VISUAL_SERVOING_CONFIG, else quaternion error in the base frame)")
    bench.set_defaults(func=_cmd_bench)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        print(f"{PROJECT_NAME} v{get_version()}")
        print(ROLE)
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
