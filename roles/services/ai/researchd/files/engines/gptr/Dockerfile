# gptr -- gpt-researcher sidecar for researchd. Reached only over the
# compose project's internal network (no published port); no ssh client, no
# docker socket, no shell tooling beyond what the Python runtime needs, runs
# as a non-root user -- same posture as ../../app/Dockerfile.
#
# arm64: gpt-researcher's own dependency closure has no torch/
# sentence-transformers (confirmed by introspecting the resolved dependency
# tree in a scratch venv -- see requirements.txt), so this builds cleanly on
# linux/arm64 with plain manylinux wheels. Do not add either package.

FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# UPSTREAM BUG, gpt-researcher 0.16.0 (the latest release as of 2026-09-07):
# actions/query_processing.py defines, at line 6,
#   def _normalize_sub_queries(parsed: Any, fallback_query: str) -> List[str]:
# but its `from typing import Any, List, Dict` sits at line 37 — BELOW the
# first use. On Python 3.12 a def's annotations are evaluated at definition
# time, so `import gpt_researcher` dies with
# `NameError: name 'Any' is not defined`. The package is unusable here
# without this patch.
#
# It goes unnoticed upstream because Python 3.14 made annotations lazy
# (PEP 649), which hides the ordering problem completely; anyone testing on
# 3.14 sees a working import. We pin 3.12, so we hit it.
#
# The guard is the IMPORT ITSELF, not a grep for the import line — a grep
# matches that late line 37 and would skip the patch while the module stays
# broken, which is exactly what happened on the first attempt. Patch only
# when the import genuinely fails, then re-verify unconditionally so a
# future upstream fix makes this a no-op and a future upstream restructure
# fails the BUILD rather than shipping an image that only breaks once
# somebody runs a job.
RUN F=/root/.local/lib/python3.12/site-packages/gpt_researcher/actions/query_processing.py \
    && if ! python -c "import gpt_researcher" >/dev/null 2>&1; then \
         sed -i '1i from typing import Any, Dict, List, Optional  # patched: see Dockerfile' "$F"; \
       fi \
    && python -c "import gpt_researcher; print('gpt_researcher imports OK')"

FROM python:3.12-slim

RUN groupadd --gid 1000 gptr \
    && useradd --uid 1000 --gid gptr --create-home --shell /usr/sbin/nologin gptr

COPY --from=builder /root/.local /home/gptr/.local

WORKDIR /app
COPY gptr/ /app/gptr/

RUN chown -R gptr:gptr /app

ENV HOME=/home/gptr \
    PATH=/home/gptr/.local/bin:$PATH \
    PYTHONPATH=/home/gptr/.local/lib/python3.12/site-packages \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GPTR_PORT=8111

USER gptr

EXPOSE 8111

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os,sys,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"GPTR_PORT\",\"8111\")}/healthz', timeout=3).status == 200 or sys.exit(1)"]

CMD ["python", "-m", "gptr.main"]
