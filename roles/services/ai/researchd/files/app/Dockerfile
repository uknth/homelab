# researchd -- fixed research pipeline, locked-down egress.
#
# Security posture this image must not undo (docs/spec/research.md):
#   - no ssh client, no docker socket, no shell tooling beyond what the
#     Python runtime itself needs
#   - no build toolchain in the final layer
#   - runs as a non-root user
#   - the only secret is RESEARCHD_LLM_API_KEY, supplied at runtime, never
#     baked into the image

FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.12-slim

RUN groupadd --gid 1000 researchd \
    && useradd --uid 1000 --gid researchd --create-home --shell /usr/sbin/nologin researchd

COPY --from=builder /root/.local /home/researchd/.local

WORKDIR /app
COPY researchd/ /app/researchd/
COPY static/ /app/static/

RUN mkdir -p /data/tree && chown -R researchd:researchd /data /app

ENV HOME=/home/researchd \
    PATH=/home/researchd/.local/bin:$PATH \
    PYTHONPATH=/home/researchd/.local/lib/python3.12/site-packages \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    RESEARCHD_DATA_DIR=/data \
    RESEARCHD_STATIC_DIR=/app/static \
    RESEARCHD_PORT=8110

USER researchd

EXPOSE 8110

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"RESEARCHD_PORT\",\"8110\")}/healthz', timeout=3).status == 200 or sys.exit(1)"]

CMD ["python", "-m", "researchd.main"]
