# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - src/hydra_umc_visual_servoing_api/servo_log.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""A record of every correction `POST /request` decides, one JSON line per frame, for tuning offline.

`configs/visual-servoing.json`:

    {"servo_log": {"file": ""}}

* `file`: where to append the lines. Empty (the default) writes nothing.

Each line holds the instant, the frame id, the outcome and its reason, the norms of the pose
error, the command (zero when it was held) and how long the decision took. No image, no pose
values and no token are written; the log is there to tune gains and limits, not to replay a job.
"""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path

CONFIG_ENV = "HYDRA_UMC_VISUAL_SERVOING_CONFIG"


class ServoLogError(ValueError):
    """The configuration file cannot be used."""


@dataclass(frozen=True)
class ServoLogPolicy:
    file: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.file, str):
            raise ServoLogError(f"servo_log.file must be text, got {self.file!r}")

    @property
    def enabled(self) -> bool:
        return bool(self.file)


def load_servo_log_policy(path: str | os.PathLike[str] | None = None) -> ServoLogPolicy:
    chosen = str(path) if path else os.environ.get(CONFIG_ENV, "")
    if not chosen:
        return ServoLogPolicy()
    try:
        data = json.loads(Path(chosen).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ServoLogError(f"cannot read the configuration file {chosen}: {error}") from error
    section = data.get("servo_log", {}) if isinstance(data, dict) else None
    if not isinstance(section, dict):
        raise ServoLogError(f"{chosen} must hold a JSON object whose servo_log entry is an object")
    return ServoLogPolicy(**({"file": section["file"]} if "file" in section else {}))


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


def make_record(
    *, issued_at_ms: int, frame_id: str | None, outcome: str, reason: str | None, linear_error: float | None,
    angular_error: float | None, command: dict, converged: bool, decision_ms: float,
) -> dict:
    return {
        "t_ms": issued_at_ms,
        "frame_id": frame_id,
        "outcome": outcome,
        "reason": reason,
        "linear_error": _finite(linear_error),
        "angular_error": _finite(angular_error),
        "command": {key: command.get(key) for key in ("vx", "vy", "vz", "wroll", "wpitch", "wyaw", "hold", "seq")},
        "converged": converged,
        "decision_ms": round(decision_ms, 3),
    }


class ServoLog:
    def __init__(self, policy: ServoLogPolicy) -> None:
        self._policy = policy
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._policy.enabled

    def write(self, record: dict) -> bool:
        """Appends one line; False (and nothing raised) when the file cannot be written: a log never stops a correction."""
        if not self._policy.enabled:
            return False
        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
        try:
            with self._lock, open(self._policy.file, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            return False
        return True
