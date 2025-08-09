# Modular Chess Stack — UI-Orchestrated (Frontend · Game Service · Engine Service)

> **TL;DR**  
> A three-service chess app where the **UI is the conductor**. The React frontend talks **directly** to:
> 1) the **Game Service** (FastAPI) for authoritative state & move validation, and  
> 2) the **Engine Service** (FastAPI) for UCI search via **Server‑Sent Events (SSE)**.  
> The two backends **never call each other**.

![UI Screenshot](docs/ui_screenshot.png)

---

## Table of Contents

- [Architecture](#architecture)
- [Services](#services)
  - [Frontend (Vite + React + MUI)](#frontend-vite--react--mui)
  - [Game Service (FastAPI + python-chess)](#game-service-fastapi--python-chess)
  - [Engine Service (FastAPI + UCI bridge)](#engine-service-fastapi--uci-bridge)
- [Local Run (Docker Compose)](#local-run-docker-compose)
- [Local Dev (without Docker)](#local-dev-without-docker)
- [API Reference](#api-reference)
  - [Game Service](#game-service)
  - [Engine Service](#engine-service)
- [Configuration](#configuration)
- [Project Layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## Architecture

**UI‑orchestrated** microservices:

```
React UI  ──►  Game Service  (apply/validate moves; returns updated FEN + legal moves)
    └────►  Engine Service  (UCI search over SSE; never mutates game)
```

- The **Frontend** owns orchestration: it posts human moves to **Game Service** and separately streams analysis/bestmoves from **Engine Service**.  
- **Game Service** holds in‑memory games, validates/apply moves with `python-chess`, and exposes a minimal REST API.  
- **Engine Service** wraps a UCI engine process behind a hardened async bridge and streams structured `info`/**bestmove** events over SSE.

---

## Services

### Frontend (Vite + React + MUI)

- Location: `frontend/`
- Dev server: `npm run dev` (Vite)
- Production: served by **nginx** in the container with reverse proxies to the two backends
- Interesting files:
  - `src/api.ts`: **single integration point** that targets **both** backends (SSE for engine; REST for game)
  - `src/App.tsx`: UI shell & event wiring
  - `nginx.conf`: proxies `/api/...` → **game-svc**, and `/engines/...` → **engine-svc**

### Game Service (FastAPI + python-chess)

- Location: `game-svc/`
- Responsibilities:
  - Create a game, expose current FEN/turn/over/result/`legalMoves`
  - Apply moves (from/to/promotion), validate legality
  - End‑state detection (mate, stalemate, insufficient material, 75‑move, repetition)
- Key modules: `app.py`, `models.py` (Pydantic DTOs), `orchestrator.py` (state + rules)

### Engine Service (FastAPI + UCI bridge)

- Location: `engine-svc/`
- Responsibilities:
  - Manage a UCI engine subprocess (configurable command)
  - Stream `info` (depth, nodes, nps, pv, score) and **bestmove** via **SSE**
  - Self‑play stream (white/black depths/rollouts)
  - Stop current search (best‑effort)
- Key modules: `app.py`, `uci_bridge.py` (async process mgmt; serialized stdout reads), `uci_parser.py` (parse `info` lines)

---

## Local Run (Docker Compose)

> Requires Docker Desktop or a recent Docker + Compose installation.

```bash
docker compose up --build
# Frontend: http://localhost:8080
# Game API (via nginx proxy): http://localhost:8080/api/...
# Engine SSE (via nginx proxy): http://localhost:8080/engines/...
```

Compose stands up three containers on the `chessnet` network:

- `frontend` → nginx serving the built UI and proxying to backends
- `game-svc` → FastAPI at `http://game-svc:8000`
- `engine-svc` → FastAPI at `http://engine-svc:8000` with a configurable UCI command

---

## Local Dev (without Docker)

### 1) Game Service

```bash
cd game-svc
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
```

### 2) Engine Service

```bash
cd engine-svc
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Example: run with the bundled reference engine
export UCI_ENGINE_CMD="python /app/uci_reference_engine.py"
uvicorn app:app --reload --port 8000
```

### 3) Frontend

```bash
cd frontend
npm install
npm run dev
# visit http://localhost:5173 (Vite); adjust API bases if hitting services directly
```

> In Docker, the UI reaches backends via nginx proxies:
> - `/api/...` → `http://game-svc:8000/api/...`
> - `/engines/...` → `http://engine-svc:8000/engines/...`

---

## API Reference

### Game Service

**Base**: `/api`

- `POST /api/games` → **Create** new game  
  **Body**: `{ "mode": "HUMAN_VS_AI" | "AI_VS_AI" | "HUMAN_VS_HUMAN" }`  
  **Returns**:  
  ```json
  {
    "gameId": "...",
    "fen": "startpos FEN or updated",
    "turn": "w|b",
    "over": false,
    "result": null,
    "legalMoves": ["e2e4","g1f3", "..."]
  }
  ```

- `GET /api/games/{gid}` → **Fetch** game state

- `POST /api/games/{gid}/moves` → **Apply** a move  
  **Body**: `{ "from": "e2", "to": "e4", "promotion": "q" }`  
  **Returns**: state as above

### Engine Service

**Base**: `/engines`

- `GET /engines/think?fen=...&side=white|black&depth=...&rollouts=...`  
  **SSE stream** of JSON events:  
  ```json
  // type:"info"
  { "type":"info","depth":12,"nodes":123456,"nps":250000,"pv":["e2e4","e7e5"],"score":{"cp":15} }
  // type:"bestmove"
  { "type":"bestmove","move":"e2e4" }
  // type:"done"
  { "type":"done" }
  ```

- `GET /engines/selfplay?fen=...&whiteDepth=...&whiteRollouts=...&blackDepth=...&blackRollouts=...`  
  **SSE** bestmove sequence for self‑play (no game writes).

- `POST /engines/stop` → request the current search to stop.

---

## Configuration

Environment variables (primarily for **engine-svc**):

- `UCI_ENGINE_PATH` — absolute path to the engine script/binary in the container, e.g. `/app/uci_reference_engine.py`
- `UCI_ENGINE_CMD` — full command used to start the engine, e.g. `python /app/uci_reference_engine.py`
- `ENGINE_READY_TIMEOUT_MS` — engine readiness timeout (default `5000`)

Frontend reverse proxy (in container):

- `frontend/nginx.conf` proxies:
  - `/api/...` → `http://game-svc:8000`
  - `/engines/...` → `http://engine-svc:8000`

---

## Project Layout

```
.
├── docker-compose.yml
├── engine-svc/
│   ├── app.py
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── uci_bridge.py
│   ├── uci_parser.py
│   └── uci_reference_engine.py
├── game-svc/
│   ├── app.py
│   ├── Dockerfile
│   ├── models.py
│   ├── orchestrator.py
│   └── requirements.txt
└── frontend/
    ├── Dockerfile
    ├── index.html
    ├── nginx.conf
    ├── package.json
    ├── tsconfig.json
    └── src/
        ├── App.tsx
        ├── api.ts
        ├── main.tsx
        └── styles.css
```

**Ports**

- UI via nginx: `http://localhost:8080`
- Backends (inside network): `game-svc:8000`, `engine-svc:8000`

---

## Troubleshooting

- **Engine stream never starts**  
  Ensure `UCI_ENGINE_CMD` is valid. The bridge preflights `isready` with a timeout (`ENGINE_READY_TIMEOUT_MS`) and will auto‑restart on failure.

- **SSE stalls / duplicate readers**  
  `uci_bridge.py` serializes stdout reads with an `asyncio.Lock` and uses a single active reader per search to prevent stalls.

- **CORS during local dev (without nginx)**  
  If you access backends from Vite’s port directly, enable CORS in FastAPI or proxy through Vite.

- **Game state not updating**  
  Remember the UI never mutates state via Engine Service; always `POST /api/games/{gid}/moves` to the Game Service.

---

## License

MIT
