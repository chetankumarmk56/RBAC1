# One image serving the whole POC: Vite builds the frontend, FastAPI serves the
# result alongside /api. App Runner runs a single container on a single port, so
# splitting them would mean two services and a CORS configuration to keep in sync.

# ---- Stage 1: build the frontend ----
FROM node:20-alpine AS frontend

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# No VITE_API_BASE_URL: the API answers on the same origin, so the relative /api
# paths in services/api.ts are already correct.
RUN npm run build


# ---- Stage 2: the service ----
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY --from=frontend /frontend/dist ./static

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Nothing is written to disk at runtime, so the app has no need for root.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080

ENTRYPOINT ["docker-entrypoint.sh"]
