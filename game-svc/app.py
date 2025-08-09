# Path: game-svc/app.py
"""
Purpose: FastAPI app for game orchestration: game lifecycle, human moves, AI thinking (SSE), and self-play.
Usage: Run with uvicorn; consumed by the frontend and docker-compose.
"""
from __future__ import annotations
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, PlainTextResponse
from typing import Optional, AsyncGenerator, Callable, Awaitable
import asyncio
import chess

from models import NewGameRequest, GameStateDTO, HumanMoveRequest
from orchestrator import store, apply_ai_move, selfplay_loop, Game
from sse import sse_json

app = FastAPI(title="game-svc", version="1.0.1")

def _ensure_game(gid: str) -> Game:
    g = store.get(gid)
    if not g:
        raise HTTPException(404, "Game not found")
    return g

@app.get("/health")
async def health():
    return PlainTextResponse("ok")

@app.post("/games")
async def create_game(req: NewGameRequest):
    g = store.create()
    # if AI vs AI and black to move? we'll just return initial state;
    # frontend can start selfplay if desired
    return JSONResponse(g.state())

@app.get("/games/{gid}")
async def get_state(gid: str):
    # Robust against undefined/null/empty ids
    if not gid or gid.lower() == "undefined" or gid.lower() == "null":
        raise HTTPException(400, "Missing or invalid game id")
    g = _ensure_game(gid)
    return JSONResponse(g.state())

@app.post("/games/{gid}/move")
async def human_move(gid: str, req: HumanMoveRequest):
    g = _ensure_game(gid)
    try:
        mv = chess.Move.from_uci(req.uci)
    except Exception:
        raise HTTPException(400, "Invalid UCI")
    if mv not in g.board.legal_moves:
        raise HTTPException(422, "Illegal move")
    g.board.push(mv)
    if g.board.is_game_over():
        g.over = True
        g.result = g.board.result(claim_draw=True)
    return JSONResponse(g.state())

@app.get("/games/{gid}/ai/think")
async def ai_think(gid: str, movetimeMs: int = 1000):
    g = _ensure_game(gid)
    async def gen():
        async for chunk, _ in apply_ai_move(g, 'white' if g.board.turn else 'black', movetimeMs):
            yield sse_json(chunk)
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.post("/games/{gid}/ai/move")
async def ai_move(gid: str, movetimeMs: int = 1000):
    g = _ensure_game(gid)
    final = None
    async for chunk, best in apply_ai_move(g, 'white' if g.board.turn else 'black', movetimeMs):
        # just drain stream; keep last best
        if best:
            final = best
    if not final:
        raise HTTPException(500, "Engine failed to produce bestmove")
    return JSONResponse(g.state())

# ---- Self-play (SSE streaming) ----

class _Sender:
    def __init__(self, send: Callable[[str], Awaitable[None]]):
        self.send = send

@app.get("/events/games/{gid}/selfplay/start")
async def selfplay_start(gid: str, whiteMs: int = 500, blackMs: int = 500):
    g = _ensure_game(gid)
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(chunk: str):
        await queue.put(chunk)

    async def gen():
        # Immediately announce start
        yield sse_json('{"stage":"start"}')
        # Kick off loop
        task = asyncio.create_task(selfplay_loop(g, whiteMs, blackMs, send))
        try:
            while True:
                chunk = await queue.get()
                yield sse_json(chunk)
                if g.over:
                    break
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if not task.done():
                task.cancel()
            yield sse_json('{"stage":"done"}')

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.post("/games/{gid}/selfplay/stop")
async def selfplay_stop(gid: str):
    # No persistent loop in this implementation; clients just close the SSE stream
    return PlainTextResponse("ok")

@app.post("/games/{gid}/selfplay/step")
async def selfplay_step(gid: str, movetimeMs: int = 500):
    g = _ensure_game(gid)
    # single engine move
    final = None
    async for chunk, best in apply_ai_move(g, 'white' if g.board.turn else 'black', movetimeMs):
        if best:
            final = best
    if not final:
        raise HTTPException(500, "Engine failed to produce bestmove")
    return JSONResponse(g.state())
