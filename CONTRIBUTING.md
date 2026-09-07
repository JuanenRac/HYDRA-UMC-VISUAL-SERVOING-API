# Contributing to HYDRA-UMC-VISUAL-SERVOING-API 🦾

We welcome contributions to the visual feedback bridge of the HYDRA-UMC platform.

## Technology Stack
- **Language**: C++20, Python 3.12.
- **Frameworks**: gRPC, Protobuf, OpenCV.
- **Hardware**: STM32H745 (SPI Bridge), Hailo-8 (Pose Output).
- **Control Theory**: PID Controllers, 6-DOF Transformation Matrices.

## Guidelines
1. **Mathematical Accuracy**: All transformation matrix logic must be validated against the standard robotics DH convention.
2. **Low Latency**: Ensure the gRPC client does not block the real-time SPI bridge.
3. **Hardware Support**: Test visual servoing loops with both Eye-in-Hand and Eye-to-Hand setups.
4. **Documentation**: Any new kinematic model must be documented with its corresponding mathematical proof.
