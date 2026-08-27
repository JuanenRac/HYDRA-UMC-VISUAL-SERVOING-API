<p align="center">
  <img src="images/HYDRA_UMC_BANNER.svg" alt="HYDRA-UMC-VISUAL-SERVOING-API banner" width="100%">
</p>

# 🎯 HYDRA-UMC-VISUAL-SERVOING-API

<p align="center">🇺🇸 <b>English</b> | <a href="README_spa.md">🇪🇸 Español</a> | <a href="README_fra.md">🇫🇷 Français</a> | <a href="README_ita.md">🇮🇹 Italiano</a> | <a href="README_deu.md">🇩🇪 Deutsch</a> | <a href="README_zho.md">🇨🇳 简体中文</a> | <a href="README_jpn.md">🇯🇵 日本語</a></p>

### 📐 Close-Loop Kinematic Correction via Image Feedback

<p align="left">
  <img src="https://img.shields.io/badge/Licencia-GPL%203.0-blue.svg" alt="GPL 3.0">
  <img src="https://img.shields.io/badge/Method-Eye--in--Hand%20%2F%20Eye--to--Hand-orange.svg" alt="Method">
  <img src="https://img.shields.io/badge/Sync-gRPC%20%2F%20SPI-yellow.svg" alt="Sync">
</p>

---

## 1. 🛠️ TECHNICAL OVERVIEW

**HYDRA-UMC-VISUAL-SERVOING-API** is the precision bridge between perception and motion. It calculates the error delta between a desired pose and the actual visual pose of an object, providing real-time kinematic corrections to the HYDRA-UMC core.

It supports both **Eye-in-Hand** (camera on tool) and **Eye-to-Hand** (fixed camera) configurations, enabling ultra-precise Pick-and-Place, SMD alignment, and dynamic trajectory adjustment.

### Key Features:
* ✅ **Real v0 - PBVS correction law:** `pose.py` + `servo.py` compute the pose delta between a current and target 6-DOF pose (shortest-turn angle wrapping, no gimbal-lock-prone long way around) and turn it into a proportional velocity command, clamped without distorting its direction. Exposed via the `correct` subcommand below - no camera or NPU needed to run or test it.
* 🛡️ **Real v0 - Safety-gated authorization:** `authorization.py` refuses to turn perception into motion unless the upstream safety state is `READY` and the visual data is fresh/confident enough. Exposed via the new `request` subcommand below - no camera, NPU, or SAFETY-ZONES process needed to run or test it.
* 🔄 **Closed-Loop Control:** Continuous feedback loop bypassing the high-level orchestrator for low latency. *(architecture goal - the gRPC feed to the HYDRA-UMC core is still future work.)*
* 📐 **Pose Estimation:** 6-DOF object pose estimation from single or multi-camera views. *(future work - needs the real Hailo-8 NPU this repo doesn't have access to yet.)*
* ⚡ **Hardware Accelerated:** Uses Hailo-8 output for instant coordinate calculation. *(future work, same reason.)*

---

## 2. 🔄 VISUAL SERVOING LOOP

```mermaid
flowchart LR
    TARGET["Target Object"] --> CAM["Camera Capture"]
    CAM --> NPU["Hailo-8 Pose Detection"]
    NPU --> API["VISUAL-SERVOING-API"]
    API --> ERROR["Error Calculation (Pose Delta)"]
    ERROR --> CORE["HYDRA-UMC Core (STM32)"]
    CORE --> MOVE["Motor Actuation"]
    MOVE --> TARGET
```

---

## 3. 🧱 ARCHITECTURE & DESIGN DECISIONS

* **Why this API has no hardware/firmware of its own.** It runs entirely on the shared CM5 + Hailo-8 module owned by the integration parent, HYDRA-UMC-VISION-NODE - no board of its own to design, so `hardware/`/`firmware/`/`os/` were pruned rather than left empty.
* **Why it's a sibling, not a submodule, of HYDRA-UMC-VISION-NODE.** Pose correction runs as its own process/deployable so a crash or slow inference cycle here can't stall the parent's own detection pipeline, which HYDRA-UMC-SAFETY-ZONES depends on for E-STOP timing.
* **Why the correction law ships before pose estimation.** Turning a pose *pair* into a bounded velocity command is pure control-theory math - it needs no camera or NPU to write or test, so v0 lands that piece (`pose.py`, `servo.py`) first. Real 6-DOF pose *estimation* needs the Hailo-8 hardware this environment doesn't have, and lands later.
* **How this fits the rest of the ecosystem.** Sits downstream of perception (HYDRA-UMC-VISION-NODE) and upstream of motion (HYDRA-UMC firmware) - turns detected offsets into the kinematic corrections the robot arm's own jog/servo loop applies.
* **Why `authorize_correction()` checks `safety_state` before confidence/freshness.** A safety fault must win over everything else, even a perfectly fresh and confident detection - so `INHIBITED` (safety_state != "READY") is checked first and short-circuits the rest of the policy. Only once the arm is confirmed safe to move does it matter whether the *data* is trustworthy enough to move it on (`REJECTED` for low confidence or stale data). This mirrors the `INHIBITED`-before-`DANGER`/`WARNING` precedence already used in HYDRA-UMC-SAFETY-ZONES.
* **Why `request` is a new subcommand instead of changing `correct`.** `correct` is the existing low-level pure-math utility (no safety awareness, no camera-freshness concept) with its own callers and tests; wrapping it in a safety gate in place would silently change its contract. `request` adds the gated, camera-facing entry point ecosystem code should actually call, while `correct` stays available unchanged for direct pose-math use.

---

## 📂 DIRECTORY STRUCTURE

CM5 + Hailo-8 is off-the-shelf hardware with no board of its own, so this
project carries no `hardware/` or `firmware/` folder. `os/` and `models/`
live only in the integration parent, `HYDRA-UMC-VISION-NODE`.

```text
HYDRA-UMC-VISUAL-SERVOING-API/
├── src/                 # Source code (hydra_umc_visual_servoing_api package)
│   └── hydra_umc_visual_servoing_api/
│       ├── pose.py           # Pose6D - 6-DOF pose (x, y, z, roll, pitch, yaw)
│       ├── servo.py          # PBVS pose-error + velocity-command correction law
│       ├── authorization.py  # Safety-gated request policy (INHIBITED/REJECTED/ACCEPTED)
│       └── main.py           # CLI entry point (bare invocation + `correct` + `request`)
├── tests/               # Real pytest suite (pose, servo, authorization, CLI)
├── docs/                # Documentation and kinematic theory
├── build/               # Build output (local .venv lives here too)
├── images/              # Media and diagrams
├── scripts/             # Utility scripts
├── tools/
│   ├── build_test.py    # Non-versioning build/compile check (no version/CHANGELOG bump)
│   └── ci_validate.py   # Manifest/CHANGELOG/docs validation used by CI
├── pyproject.toml       # Package metadata, dependencies, odometer version
├── bump_version.py      # Odometer-style version bump (run by build.sh/.bat)
├── build.sh / build.bat # venv + editable install + compile-check + tests
├── build-test.sh / build-test.bat # Non-versioning build check (no CHANGELOG/version bump)
└── run.sh / run.bat     # Runs the entry point from the local venv
```

---

## 🏗️ BUILD & RUN

Requires Python 3.10+.

```bash
# Linux / macOS
./build.sh   # bumps the odometer version, creates .venv, installs the
             # package editable (with dev extras), compile-checks every
             # file under src/, and runs the real pytest suite
./run.sh     # runs the entry point from .venv, prints name + version + role
```

```bat
:: Windows
build.bat
run.bat
```

`build.sh`/`build.bat` bump this project's own `pyproject.toml` version
using the ecosystem-wide "odometer" rule (PATCH+1, carrying into MINOR past
9) before every real build, then compile-check the source with
`python -m compileall`.

Real example - compute the correction from a current pose to a target one:

```bash
./run.sh correct --current "0,0,0.5,0,0,0" --target "0.02,-0.01,0.48,0,0,0.05" \
  --gain 0.8 --max-linear-speed 0.05
# pose error   : dx=0.020000 dy=-0.010000 dz=-0.020000  droll=0.000000 dpitch=0.000000 dyaw=0.050000
# error norm   : linear=0.030000 m  angular=0.050000 rad
# velocity cmd : vx=0.016000 vy=-0.008000 vz=-0.016000  wroll=0.000000 wpitch=0.000000 wyaw=0.040000
# converged    : False
```

Real example - request a safety-gated correction (accepted, inhibited, and rejected):

```bash
./run.sh request --current "0,0,0,0,0,0" --target "1,0,0,0,0,0" \
  --frame-id cam0-f42 --confidence 0.9 --data-age-ms 30 --safety-state READY
# outcome : ACCEPTED - frame 'cam0-f42' authorized (confidence=0.9, data_age_ms=30.0)
# pose error   : dx=1.000000 dy=0.000000 dz=0.000000  droll=0.000000 dpitch=0.000000 dyaw=0.000000
# velocity cmd : vx=1.000000 vy=0.000000 vz=0.000000  wroll=0.000000 wpitch=0.000000 wyaw=0.000000

./run.sh request --current "0,0,0,0,0,0" --target "1,0,0,0,0,0" \
  --frame-id cam0-f42 --confidence 0.9 --data-age-ms 30 --safety-state FAULT
# outcome : INHIBITED - safety_state is 'FAULT', not 'READY'   (exit code 2)

./run.sh request --current "0,0,0,0,0,0" --target "1,0,0,0,0,0" \
  --frame-id cam0-f42 --confidence 0.2 --data-age-ms 30 --safety-state READY
# outcome : REJECTED - confidence 0.2 is below the required minimum 0.6 for frame 'cam0-f42'   (exit code 1)
```

---

## ✅ Current Status & Next Steps

**Real today:** the PBVS pose-error and velocity-command correction law
(`pose.py`, `servo.py`) - the "Error Calculation (Pose Delta)" step in
the loop diagram above - with a real `correct` CLI command; and the
safety-gated authorization policy (`authorization.py`) that refuses to
turn a visual detection into motion unless the upstream safety state is
`READY` and the data is confident/fresh enough, exposed via the `request`
CLI command. 40 tests total.

**Still ahead, and blocked on real hardware:** 6-DOF pose *estimation*
from camera frames (needs the Hailo-8 NPU), and the low-latency gRPC
feed of the resulting velocity command to the HYDRA-UMC core.

## 🚀 ROADMAP
* **Phase 1:** Multi-camera pipeline synchronization and calibration for 8x USB 3.0 feeds.
* **Phase 2:** Migration to YOLOv11 and Hailo-8L optimization for industrial component detection.
* **Phase 3:** Real-time 3D reconstruction from stereo vision nodes and safety zone dynamic mapping.
* **Phase 4:** Support for 9-DOF visual tracking (including orientation redundancy) and sub-micrometric correction.

---

## 🔗 Related Projects

This project is part of a larger robotics ecosystem by the same author (JuanenRac / Electro Hobby 3D), spanning firmware, control software, AI nodes, and fleet tooling. Worth knowing about, since a request might actually be about one of these rather than this repository.

### Family

**Parent:** **[HYDRA-UMC-VISION-NODE](https://github.com/JuanenRac/HYDRA-UMC-VISION-NODE)** — the integration parent this API turns perception into pose corrections for.

**Siblings:**
- **[HYDRA-UMC-VISION-STREAMER](https://github.com/JuanenRac/HYDRA-UMC-VISION-STREAMER)** — captures and pre-processes the camera feeds the parent consumes.
- **[HYDRA-UMC-DETECTION-HEF](https://github.com/JuanenRac/HYDRA-UMC-DETECTION-HEF)** — compiles the `.hef` models the parent loads onto its Hailo-8 NPU.
- **[HYDRA-UMC-SAFETY-ZONES](https://github.com/JuanenRac/HYDRA-UMC-SAFETY-ZONES)** — turns the parent's perception into intrusion detection and E-STOP triggers.

### Directly Related (outside the family)

- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — sends kinematic pose corrections to this firmware.

### Rest of the Ecosystem

**HYDRA-UMC platform** — the multi-robot micro-factory cell
- **[HYDRA-UMC](https://github.com/JuanenRac/HYDRA-UMC)** — the CM5 + STM32H745 motherboard orchestrating up to 8 robot arms.
- **[HYDRA-UMC-SERVER](https://github.com/JuanenRac/HYDRA-UMC-SERVER)** — the Express/WebSocket backend every control client talks to.
- **[HYDRA-UMC-STUDIO](https://github.com/JuanenRac/HYDRA-UMC-STUDIO)** — web-based control dashboard, multi-robot 3D visualization.
- **[HYDRA-UMC-ANDROID-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-ANDROID-CONTROL)** — Android control app over Wi-Fi/Bluetooth.
- **[HYDRA-UMC-IOS-CONTROL](https://github.com/JuanenRac/HYDRA-UMC-IOS-CONTROL)** — iOS/iPadOS control app built in Flutter.
- **[HYDRA-UMC-SUITE](https://github.com/JuanenRac/HYDRA-UMC-SUITE)** — desktop swarm command center (Python/PySide6).
- **[HYDRA-UMC-EDITOR-URDF](https://github.com/JuanenRac/HYDRA-UMC-EDITOR-URDF)** — desktop URDF model editor for the robot catalog.
- **[HYDRA-UMC-DSI](https://github.com/JuanenRac/HYDRA-UMC-DSI)** — native touch UI for the onboard DSI touchscreen.

**URTC platform** — the tool head controller every HYDRA-UMC robot arm carries
- **[URTC](https://github.com/JuanenRac/URTC)** — CAN bus tool head controller, 25 tool profiles.
- **[URTC-FLASHER](https://github.com/JuanenRac/URTC-FLASHER)** — desktop CAN-OTA + SWD/JTAG flashing tool.
- **[URTC-TESTER](https://github.com/JuanenRac/URTC-TESTER)** — desktop live CAN-bus diagnostic tool.
- **[URTC-WEB-STUDIO](https://github.com/JuanenRac/URTC-WEB-STUDIO)** — browser-based alternative via Web Serial API.

**🧠 Cognitive AI Node (Hailo-10)**
- [HYDRA-UMC-COGNITIVE-NODE](https://github.com/JuanenRac/HYDRA-UMC-COGNITIVE-NODE)
- [HYDRA-UMC-VLA-ENGINE](https://github.com/JuanenRac/HYDRA-UMC-VLA-ENGINE)
- [HYDRA-UMC-VOICE-UI](https://github.com/JuanenRac/HYDRA-UMC-VOICE-UI)
- [HYDRA-UMC-SEMANTIC-PLANNER](https://github.com/JuanenRac/HYDRA-UMC-SEMANTIC-PLANNER)
- [HYDRA-UMC-DOCS-QA](https://github.com/JuanenRac/HYDRA-UMC-DOCS-QA)

**🐝 Orchestration & Swarm**
- [HYDRA-UMC-ORCHESTRATOR](https://github.com/JuanenRac/HYDRA-UMC-ORCHESTRATOR)
- [HYDRA-UMC-SWARM-SYNC](https://github.com/JuanenRac/HYDRA-UMC-SWARM-SYNC)
- [HYDRA-UMC-PATH-PLANNER-3D](https://github.com/JuanenRac/HYDRA-UMC-PATH-PLANNER-3D)
- [HYDRA-UMC-JOB-DISPATCHER](https://github.com/JuanenRac/HYDRA-UMC-JOB-DISPATCHER)
- [HYDRA-UMC-NODE-HEALING](https://github.com/JuanenRac/HYDRA-UMC-NODE-HEALING)

**🎮 Digital Twin & Simulation**
- [HYDRA-UMC-TWIN](https://github.com/JuanenRac/HYDRA-UMC-TWIN)
- [HYDRA-UMC-PHYSICS-REPLICA](https://github.com/JuanenRac/HYDRA-UMC-PHYSICS-REPLICA)
- [HYDRA-UMC-HIL-BRIDGE](https://github.com/JuanenRac/HYDRA-UMC-HIL-BRIDGE)
- [HYDRA-UMC-SYNTHETIC-DATA-GEN](https://github.com/JuanenRac/HYDRA-UMC-SYNTHETIC-DATA-GEN)

**📊 Data & Analytics**
- [HYDRA-UMC-DATALAKE](https://github.com/JuanenRac/HYDRA-UMC-DATALAKE)
- [HYDRA-UMC-TELEMETRY-COLLECTOR](https://github.com/JuanenRac/HYDRA-UMC-TELEMETRY-COLLECTOR)
- [HYDRA-UMC-ANOMALY-DETECTOR](https://github.com/JuanenRac/HYDRA-UMC-ANOMALY-DETECTOR)
- [HYDRA-UMC-PRODUCTION-REPORTS](https://github.com/JuanenRac/HYDRA-UMC-PRODUCTION-REPORTS)

**🏭 Industrial Gateway**
- [HYDRA-UMC-GATEWAY-INDUSTRIAL](https://github.com/JuanenRac/HYDRA-UMC-GATEWAY-INDUSTRIAL)
- [HYDRA-UMC-OPCUA-SERVER](https://github.com/JuanenRac/HYDRA-UMC-OPCUA-SERVER)
- [HYDRA-UMC-MQTT-BROKER](https://github.com/JuanenRac/HYDRA-UMC-MQTT-BROKER)
- [HYDRA-UMC-MTCONNECT-ADAPTER](https://github.com/JuanenRac/HYDRA-UMC-MTCONNECT-ADAPTER)

**🛠️ Complementary Tools**
- [URTC-SMART-RACK](https://github.com/JuanenRac/URTC-SMART-RACK)
- [URTC-VISION-TOOL](https://github.com/JuanenRac/URTC-VISION-TOOL)
- [HYDRA-UMC-WATCH](https://github.com/JuanenRac/HYDRA-UMC-WATCH)
- [HYDRA-UMC-TOOL-CLI](https://github.com/JuanenRac/HYDRA-UMC-TOOL-CLI)
- [HYDRA-UMC-DASHBOARD-AI](https://github.com/JuanenRac/HYDRA-UMC-DASHBOARD-AI)


## 👤 AUTHOR
**JuanenRac** (Electro Hobby 3D)
📧 electrohobby3d@gmail.com

## 📜 LICENSE
GPL-3.0 - See LICENSE for details.

## 🛠️ BUILD & RUN

Use the non-versioning build check before a release build:

| Action | Windows | Linux / macOS |
|---|---|---|
| Build check (no version or CHANGELOG change) | `build-test.bat` | `./build-test.sh` |
| Run / development (when provided) | `run*.bat` or `dev*.bat` | `./run*.sh` or `./dev*.sh` |

`build-test.bat` and `build-test.sh` compile or validate the project stack without incrementing `hydra-umc.project.json` or modifying `CHANGELOG.md`. They may create normal compiler output only. Existing `build*.bat`, `build*.sh`, `run*` and `dev*` scripts retain their project-specific, versioned or runtime behavior; use them when that behavior is required.