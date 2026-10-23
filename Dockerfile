# DIA - Discovery Intake Agent (Gemini ADK) for Google Cloud Run.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

# WeasyPrint (formatted PDF reports) needs Pango/HarfBuzz; antiword lets the
# upload tray read legacy Word .doc files.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libjpeg62-turbo \
        shared-mime-info antiword \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8080

# Cloud Run overrides PORT at runtime; app.py honours it.
CMD ["python", "app.py"]
