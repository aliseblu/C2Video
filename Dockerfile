# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 AS studio
WORKDIR /build/studio
COPY studio/package*.json ./
RUN npm ci --no-audit --no-fund
COPY studio/ ./
RUN npm run build

FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/browsers C2VIDEO_ENV=production \
    C2VIDEO_WORK_DIR=/data/work C2VIDEO_FINAL_DIR=/data/final \
    C2VIDEO_WORKER_MODE=external TTS_PROVIDER=edge
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements-runtime.txt ./
RUN python -m pip install --no-cache-dir -r requirements-runtime.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 c2video \
    && useradd --uid 10001 --gid c2video --create-home c2video \
    && mkdir -p /data/work /data/final \
    && chown -R c2video:c2video /data \
    && chmod -R a+rX /opt/browsers
COPY pyproject.toml README.md ./
COPY c2video/ ./c2video/
COPY --from=studio /build/c2video/api/static/ ./c2video/api/static/
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation . \
    && python -m pip check \
    && rm -rf /app/build /app/c2video.egg-info
USER 10001:10001
EXPOSE 8765
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-m", "c2video.deployment"]
CMD ["api"]

