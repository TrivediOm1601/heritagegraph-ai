# ---- Stage 1: build the React frontend ------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: Python backend + built frontend, one origin -----------------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY database/ database/
COPY --from=frontend /app/frontend/dist frontend/dist
WORKDIR /app/backend
# Platforms such as Render inject $PORT. Migrate + seed on every start
# (idempotent), then serve the API and the website on one port.
CMD ["sh", "-c", "python -m scripts.run_server --migrate --seed && python -m scripts.run_server --host 0.0.0.0 --port ${PORT:-8000}"]
