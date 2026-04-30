# Meridian Support — Cloud Run (Streamlit)
# https://cloud.google.com/run/docs/containerizing
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-prod.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements-prod.txt

COPY app.py .
COPY meridian/ meridian/

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Cloud Run sets PORT; Streamlit must bind 0.0.0.0 for external traffic.
CMD ["sh", "-c", "exec streamlit run app.py \
  --server.port=\"${PORT}\" \
  --server.address=0.0.0.0 \
  --server.headless=true \
  --browser.gatherUsageStats=false"]
