# Path: engine-svc/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=500 \
    PIP_RETRIES=10

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Default to loader+AB; docker-compose can override to another engine.
ENV UCI_ENGINE_CMD="python /app/uci_main.py --engine ab"

EXPOSE 8001
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8001"]