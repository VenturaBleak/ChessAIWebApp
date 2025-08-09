# game-svc/Dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=500 \
    PIP_RETRIES=10

WORKDIR /app

COPY requirements.txt ./

# Harden installs against flaky PyPI timeouts (no self-upgrade of pip)
RUN python -m pip install --no-cache-dir \
      --retries ${PIP_RETRIES} --timeout ${PIP_DEFAULT_TIMEOUT} \
      --index-url https://pypi.org/simple \
      -r requirements.txt

COPY . .
EXPOSE 8000
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8000"]