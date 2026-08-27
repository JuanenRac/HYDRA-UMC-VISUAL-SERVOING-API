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
