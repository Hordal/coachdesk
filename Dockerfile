FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data ENABLE_FIXTURES=0
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 coach && mkdir /data && chown coach:coach /data
WORKDIR /app
COPY backend/requirements.lock.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY backend/ /app/backend/
COPY --from=frontend /build/dist/ /app/frontend/dist/
USER coach
WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.api.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
