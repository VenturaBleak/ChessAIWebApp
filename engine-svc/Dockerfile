# engine-svc/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=500 \
    PIP_RETRIES=10

WORKDIR /app

COPY requirements.txt ./

# ⚠️ Do NOT self-upgrade pip/setuptools here — flaky networks can stall on files.pythonhosted.org
RUN python -m pip install --no-cache-dir --retries ${PIP_RETRIES} --timeout ${PIP_DEFAULT_TIMEOUT} \
      --index-url https://pypi.org/simple \
      -r requirements.txt

COPY . .

ENV UCI_ENGINE_PATH=/app/uci_reference_engine.py
ENV UCI_ENGINE_CMD="python /app/uci_reference_engine.py"

EXPOSE 8001
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8001"]