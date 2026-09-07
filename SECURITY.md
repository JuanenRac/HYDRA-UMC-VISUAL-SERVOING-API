# Security Policy 🔒 (HYDRA-UMC-VISUAL-SERVOING-API)

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x.x  | ✅ Yes             |

## Reporting a Vulnerability

**CRITICAL: Do not report safety-critical vulnerabilities through public GitHub issues.**

In a motion-linked vision system, a security flaw can lead to physical collisions or precision loss. If you discover a vulnerability affecting the **transformation math**, **gRPC pose injection**, or **SPI buffer overflows**:

1. **Email**: Send a detailed report to `electrohobby3d@gmail.com`.
2. **Impact**: Describe if the bug allows injecting malicious pose deltas, bypassing kinematic limits, or crashing the motion bridge.
3. **Response**: Initial acknowledgment within 48 hours.

We follow a coordinated disclosure policy to ensure hardware safety before public release.
