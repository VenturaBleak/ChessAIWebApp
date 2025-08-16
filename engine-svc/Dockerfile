# ----- Stage 1: build the Rust engine -----
FROM rust:1.84-slim AS rust-builder
WORKDIR /src

# (Better layer caching) copy ONLY Cargo.toml if you don't have a lockfile yet
# 👇 removed Cargo.lock from this COPY
COPY engines/ab_engine_rust/Cargo.toml ./engines/ab_engine_rust/

# dummy build to cache deps
RUN mkdir -p engines/ab_engine_rust/src \
 && printf "fn main() {}\n" > engines/ab_engine_rust/src/main.rs \
 && cargo build --release --manifest-path engines/ab_engine_rust/Cargo.toml \
 && rm -f engines/ab_engine_rust/src/main.rs

# now copy real sources and build release
COPY engines/ab_engine_rust/ ./engines/ab_engine_rust/
RUN cargo build --release --manifest-path engines/ab_engine_rust/Cargo.toml

# ----- Stage 2: your existing Python runtime -----
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=500 \
    PIP_RETRIES=10

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

# copy your Python code
COPY . .

# copy the Rust binary in (no toolchain in final image)
# (cargo puts target next to the package when using --manifest-path)
COPY --from=rust-builder /src/engines/ab_engine_rust/target/release/ab_engine_rust /app/ab_engine_rust

# default stays Python AB via loader; compose can switch to Rust binary
ENV UCI_ENGINE_CMD="python /app/uci_main.py --engine ab"

EXPOSE 8001
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8001"]