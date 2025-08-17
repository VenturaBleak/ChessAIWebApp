# ===== Stage 1: build the Rust engine =====
FROM rust:1.84-slim AS rust-builder
WORKDIR /src

ARG ENGINE_DIR=engines/ab_engine_rust
ARG ENGINE_BIN=pyrefengine

# Put cargo build outputs in a known place
ENV CARGO_TARGET_DIR=/cargo-target

# Cache deps
COPY ${ENGINE_DIR}/Cargo.* ./${ENGINE_DIR}/
RUN cargo fetch --manifest-path ${ENGINE_DIR}/Cargo.toml

# Build just the bin
COPY ${ENGINE_DIR}/ ./${ENGINE_DIR}/
RUN cargo build --release --manifest-path ${ENGINE_DIR}/Cargo.toml --bin ${ENGINE_BIN}

# ===== Stage 2: Python runtime =====
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=500 \
    PIP_RETRIES=10

RUN useradd -m -u 10001 appuser
WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

# Re-declare ARGs if you want to keep them configurable here too
ARG ENGINE_BIN=pyrefengine

# Copy from the fixed cargo target dir set in the builder
COPY --from=rust-builder /cargo-target/release/${ENGINE_BIN} /app/${ENGINE_BIN}

ENV UCI_ENGINE_CMD="python /app/uci_main.py --engine ab"
USER appuser
EXPOSE 8001
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8001"]
