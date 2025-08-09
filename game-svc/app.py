# Path: game-svc/app.py
"""
FastAPI app for the Chess game service — **UI-only orchestration**

Responsibilities:
- Game lifecycle (create, fetch)
- Apply moves (human or engine-proposed)

This service does **not** communicate with the engine. The frontend talks to:
- Game Service for state/moves
- Engine Service for bestmove SSE

Endpoint base: /api
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from models import NewGameRequest, MoveRequest, GameStateDTO
from orchestrator import STORE, apply_move

app = FastAPI(title="game-svc", version="1.0")


# ————— helpers —————

def _ensure_game(gid: str):
    try:
        return STORE.get(gid)
    except KeyError:
        raise HTTPException(404, f"game {gid} not found")


# ————— routes —————

@app.post("/api/games", response_model=GameStateDTO)
def new_game(req: NewGameRequest):
    g = STORE.new(req.mode)
    return JSONResponse(g.state())


@app.get("/api/games/{gid}", response_model=GameStateDTO)
def get_state(gid: str):
    g = _ensure_game(gid)
    return JSONResponse(g.state())


@app.post("/api/games/{gid}/move", response_model=GameStateDTO)
def post_move(gid: str, body: MoveRequest):
    g = _ensure_game(gid)
    try:
        apply_move(g, body.from_square, body.to_square, body.promotion)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse(g.state())