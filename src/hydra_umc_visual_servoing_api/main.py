# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
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

import argparse
import sys
from importlib.metadata import PackageNotFoundError, version

from .api import VisualServoingServer
from .authorization import (
    AuthorizationPolicy,
    RequestOutcome,
    VisualTargetRequest,
    authorize_correction,
)
from .pose import Pose6D, PoseParseError
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


def _cmd_correct(args: argparse.Namespace) -> int:
    try:
        current = Pose6D.parse(args.current)
        target = Pose6D.parse(args.target)
    except PoseParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    error = compute_pose_error(current, target)
    command = compute_velocity_command(
        error, gain=args.gain,
        max_linear_speed=args.max_linear_speed,
        max_angular_speed=args.max_angular_speed,
    )
    converged = is_converged(error, args.linear_tol, args.angular_tol)

    print(f"pose error   : dx={error.dx:.6f} dy={error.dy:.6f} dz={error.dz:.6f}  "
          f"droll={error.droll:.6f} dpitch={error.dpitch:.6f} dyaw={error.dyaw:.6f}")
    print(f"error norm   : linear={error.linear_norm:.6f} m  angular={error.angular_norm:.6f} rad")
    print(f"velocity cmd : vx={command.vx:.6f} vy={command.vy:.6f} vz={command.vz:.6f}  "
          f"wroll={command.wroll:.6f} wpitch={command.wpitch:.6f} wyaw={command.wyaw:.6f}")
    print(f"converged    : {converged}")
    return 0


def _cmd_request(args: argparse.Namespace) -> int:
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

    decision = authorize_correction(
        request, policy, gain=args.gain,
        max_linear_speed=args.max_linear_speed,
        max_angular_speed=args.max_angular_speed,
    )

    print(f"outcome : {decision.outcome.value.upper()} - {decision.reason}")

    if decision.outcome is not RequestOutcome.ACCEPTED:
        return 2 if decision.outcome is RequestOutcome.INHIBITED else 1

    error = decision.error
    command = decision.command
    print(f"pose error   : dx={error.dx:.6f} dy={error.dy:.6f} dz={error.dz:.6f}  "
          f"droll={error.droll:.6f} dpitch={error.dpitch:.6f} dyaw={error.dyaw:.6f}")
    print(f"velocity cmd : vx={command.vx:.6f} vy={command.vy:.6f} vz={command.vz:.6f}  "
          f"wroll={command.wroll:.6f} wpitch={command.wpitch:.6f} wyaw={command.wyaw:.6f}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    server = VisualServoingServer((args.addr, args.port), ROLE)
    print(f"[visual-servoing-api] HTTP API listening on {args.addr}:{args.port}")
    print("[visual-servoing-api] POST /correct, POST /request, GET /stats")
    try:
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
    correct.add_argument("--max-linear-speed", type=float, default=None, dest="max_linear_speed",
                          help="Clamp the linear velocity command's norm (m/s)")
    correct.add_argument("--max-angular-speed", type=float, default=None, dest="max_angular_speed",
                          help="Clamp the angular velocity command's norm (rad/s)")
    correct.add_argument("--linear-tol", type=float, default=0.001, dest="linear_tol",
                          help="Linear convergence tolerance in meters (default: 0.001)")
    correct.add_argument("--angular-tol", type=float, default=0.01, dest="angular_tol",
                          help="Angular convergence tolerance in radians (default: 0.01)")
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
    request.add_argument("--max-linear-speed", type=float, default=None, dest="max_linear_speed",
                          help="Clamp the linear velocity command's norm (m/s)")
    request.add_argument("--max-angular-speed", type=float, default=None, dest="max_angular_speed",
                          help="Clamp the angular velocity command's norm (rad/s)")
    request.set_defaults(func=_cmd_request)

    serve = subparsers.add_parser(
        "serve",
        help="Run the real PBVS correction law and authorization gate as a JSON/HTTP API "
             "(POST /correct, POST /request) - the same functions the 'correct'/'request' "
             "subcommands run, reachable from a real caller instead of one-shot CLI args.",
    )
    serve.add_argument("--addr", default="127.0.0.1", help="address to bind the HTTP API to")
    serve.add_argument("--port", type=int, default=8091, help="port for the HTTP API")
    serve.set_defaults(func=_cmd_serve)

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
