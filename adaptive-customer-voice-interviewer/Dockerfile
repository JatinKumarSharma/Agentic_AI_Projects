FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent ./agent
COPY frontend ./frontend
COPY scripts ./scripts
COPY api.py ./


FROM base AS runtime

ENV CVI_READ_MODEL_DB=/data/research_read_model.sqlite3

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin cvi \
    && mkdir -p /data /backups \
    && chown -R cvi:cvi /app /data /backups

USER cvi

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS test

ENV CVI_READ_MODEL_DB=/tmp/cvi-test.sqlite3 \
    GROQ_API_KEY=dummy-for-offline-tests

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY deploy ./deploy
COPY tests ./tests

CMD ["python", "-m", "pytest", "-q", "-rs"]
