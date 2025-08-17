# ChessAIWebApp

![Python](https://img.shields.io/badge/Python-3.10%2B-informational)  ![Rust](https://img.shields.io/badge/Rust-stable-informational)  ![React](https://img.shields.io/badge/React-frontend-informational)

This repository provides a **full‑stack chess app**: a React frontend, Python FastAPI services for game state and engine bridging, and a Rust chess engine.

## 🎮 Demo
![Chess Demo](docs/ui_gif.gif)

### Screenshot
![Chess UI Screenshot](docs/ui_screenshot1.png)
![Chess UI Screenshot](docs/ui_screenshot2.png)

## 🧩 Components
- **Frontend (React)**: Web UI for the chessboard, moves, and game state (see `frontend/`).
- **Game State Service (Python · FastAPI)**: Validates moves and tracks games; exposes a REST API (see `game-svc/`).
- **Engine Wrapper Service (Python · FastAPI)**: Translates board state to the engine and returns best moves (see `engine-svc/`).
- **Chess Engine (Rust)**: Alpha–beta search with a domain-specific evaluation (`engine-svc/engines/ab_engine_rust/`).

## 🧠 How it works (high level)
1. The **frontend** sends game actions to the **Game State Service**.
2. The **Game State Service** maintains/validates the position and calls the **Engine Wrapper** for AI moves.
3. The **Engine Wrapper** invokes the **Rust engine** and returns the selected move.

## 🧭 API at a glance
**Game State Service**
- `GET /api/games/{gid}`
- `POST /api/games`
- `POST /api/games/{gid}/move`

**Engine Wrapper Service**
- `GET /engines/selfplay`
- `GET /engines/think`
- `GET /health`
- `POST /engines/stop`

## 📂 Project Structure
```
./
  docs/
  engine-svc/
  frontend/
  game-svc/
  docker-compose.yml
  docs/
    ui_gif.gif
    ui_screenshot1.png
    ui_screenshot2.png
  engine-svc/
    engines/
    .dockerignore
    Dockerfile
    app.py
    requirements.txt
    uci_bridge.py
    uci_main.py
    uci_parser.py
    engine-svc/engines/
      ab_engine.py
      base.py
  frontend/
    src/
    Dockerfile
    index.html
    nginx.conf
    package.json
    tsconfig.json
    frontend/src/
      App.tsx
      api.ts
      main.tsx
      styles.css
  game-svc/
    Dockerfile
    app.py
    models.py
    orchestrator.py
    requirements.txt
```

## 🚀 Getting Started

**With Docker Compose**
```bash
docker compose up --build
```
Services after startup:
- `game-svc` → http://localhost:8000
- `frontend` → http://localhost:8080


## 🐳 Docker
- `docker-compose.yml` present. Use `docker compose up` to run all services together.

## 📄 License
MIT License
