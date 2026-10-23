# DIA - Discovery Intake Agent (Gemini ADK) for Google Cloud Run.
# Pinned to the multi-arch index digest of python:3.12-slim as of 2026-10-22
# (registry-1.docker.io). Bump the digest deliberately (e.g. via Dependabot/Renovate).
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements.txt requirements-security-overrides.txt ./
# Second step applies CVE overrides that google-adk's upper bounds would otherwise block.
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install --no-deps -r requirements-security-overrides.txt

COPY . .

EXPOSE 8080

# Cloud Run overrides PORT at runtime; app.py honours it.
CMD ["python", "app.py"]
