# =============================================================================
# HYDRA-UMC-VISUAL-SERVOING-API - Container Build: Dockerfile
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
# Real, minimal image for the PBVS correction/authorization HTTP API
# (api.py's own VisualServoingServer, stdlib http.server -
# pyproject.toml's own dependencies is deliberately []). Same
# --addr/--port CLI the real CM5 systemd unit
# (systemd/hydra-umc-visual-servoing-api.service) already runs, just
# bound to 0.0.0.0 instead of 127.0.0.1 - a container's own network
# namespace already isolates it the way the systemd unit's loopback bind
# does on bare metal, and 127.0.0.1 inside a container would be
# unreachable from HYDRA-UMC-VISION-NODE's own container over the
# compose network. Non-root, matching that same unit's own
# User=hydra-umc-visual-servoing-api. Consumed by
# HYDRA-UMC-VISION-NODE's own docker-compose.yml as the
# "visual-servoing-api" service.

FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE.md ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN useradd --system --create-home --home-dir /home/hydra hydra
USER hydra

EXPOSE 8091
ENTRYPOINT ["hydra-umc-visual-servoing-api"]
CMD ["serve", "--addr", "0.0.0.0", "--port", "8091"]
