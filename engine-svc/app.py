# Path: engine-svc/app.py
"""
Engine Service (UCI) — UI-orchestrated, no Game Service calls.

Exposes:
  - GET  /health                     -> {"ok": true}
  - GET  /engines/think              -> SSE: {type:"info"| "bestmove"| "done"}
        ?fen=&side=white|black&depth=&rollouts=
  - GET  /engines/selfplay           -> SSE bestmove sequence (no game writes)
        ?fen=&whiteDepth=&whiteRollouts=&blackDepth=&blackRollouts=
  - POST /engines/stop               -> stop current search/stream (best-effort)

Notes:
  * This service NEVER mutates game state and NEVER calls the Game Service.
  * SSE events are tiny JSON objects, one per `data:` line.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator, Optional

import chess
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse, JSONResponse

from uci_bridge import UciBridge

print("[DBG] app.py loaded", flush=True)
app = FastAPI(title="engine-svc", version="1.0")
print("[DBG] FastAPI app created", flush=True)

# Single engine process shared per instance
ENGINE_CMD = os.getenv("UCI_ENGINE_CMD") or f"python {os.path.abspath(os.path.join(os.path.dirname(__file__), 'uci_reference_engine.py'))}"
print(f"[DBG] ENGINE_CMD={ENGINE_CMD}", flush=True)
bridge = UciBridge(ENGINE_CMD)
print("[DBG] UciBridge instantiated", flush=True)

# Global stop flag (best-effort for current client streams)
_stop_all = asyncio.Event()

@app.get("/health")
async def health():
    return {"ok": True}

@app.post("/engines/stop")
async def engines_stop():
    """Stop current search or selfplay stream (best-effort)."""
    _stop_all.set()
    try:
        await bridge.abort_current_search()
    except Exception:
        pass
    # small delay so in-flight generators notice
    await asyncio.sleep(0.05)
    _stop_all.clear()
    return {"ok": True}

def _sse_json(obj: dict) -> str:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n"

@app.get("/engines/think")
async def engines_think(
    fen: str = Query(..., description="Position as FEN"),
    side: Optional[str] = Query(None, regex="^(white|black)$"),
    depth: int = Query(6, ge=1),
    rollouts: int = Query(150, ge=0),
) -> StreamingResponse:
    # Debug: log request params
    print(f"[ENGINE] think req fen='{fen}' side={side} depth={depth} rollouts={rollouts}", flush=True)
    # (validation unchanged)
    try:
        board = chess.Board(fen)
    except Exception:
        print("[ENGINE] think invalid FEN", flush=True)
        raise HTTPException(400, "Invalid FEN")

    if side is not None:
        stm = "white" if board.turn == chess.WHITE else "black"
        mismatch = (stm != side)
    else:
        mismatch = False

    async def gen() -> AsyncGenerator[str, None]:
        if mismatch:
            print("[ENGINE] think: side mismatch warning", flush=True)
            yield _sse_json({"type": "info", "warning": "side parameter does not match FEN turn"})

        async for chunk in bridge.think_stream(fen, depth=depth, rollouts=rollouts, movetime_ms=None):
            try:
                msg = json.loads(chunk)
            except Exception:
                print(f"[ENGINE] think: bad chunk {chunk!r}", flush=True)
                continue
            stage = msg.get("stage")
            if stage == "searching":
                yield _sse_json({"type": "info", **{k:v for k,v in msg.items() if k!='stage'}})
            elif stage == "done":
                bm = msg.get("bestmove")
                print(f"[ENGINE] think: bestmove={bm}", flush=True)
                if bm:
                    yield _sse_json({"type": "bestmove", "move": bm})
                yield _sse_json({"type": "done"})
                print("[ENGINE] think: done", flush=True)
                break
            elif stage == "error":
                print(f"[ENGINE] think: error {msg}", flush=True)
                yield _sse_json({"type": "info", "error": msg.get("message", "engine error")})
                yield _sse_json({"type": "done"})
                break

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.get("/engines/selfplay")
async def engines_selfplay(
    fen: str = Query(..., description="Start position as FEN"),
    whiteDepth: int = Query(6, ge=1),
    whiteRollouts: int = Query(150, ge=0),
    blackDepth: int = Query(6, ge=1),
    blackRollouts: int = Query(150, ge=0),
) -> StreamingResponse:
    print(f"[ENGINE] selfplay req fen='{fen}' wd={whiteDepth}/{whiteRollouts} bd={blackDepth}/{blackRollouts}", flush=True)
    try:
        board = chess.Board(fen)
    except Exception:
        print("[ENGINE] selfplay invalid FEN", flush=True)
        raise HTTPException(400, "Invalid FEN")

    async def gen() -> AsyncGenerator[str, None]:
        _stop_all.clear()
        while True:
            if _stop_all.is_set():
                print("[ENGINE] selfplay: stop signal", flush=True)
                yield _sse_json({"type": "done"})
                break
            if board.is_game_over(claim_draw=True):
                print("[ENGINE] selfplay: game over", flush=True)
                yield _sse_json({"type": "done"})
                break

            side_flag = "w" if board.turn == chess.WHITE else "b"
            d, r = (whiteDepth, whiteRollouts) if side_flag == "w" else (blackDepth, blackRollouts)
            print(f"[ENGINE] selfplay: think side={side_flag} depth={d} rollouts={r}", flush=True)

            async for chunk in bridge.think_stream(board.fen(), depth=d, rollouts=r, movetime_ms=None):
                if _stop_all.is_set():
                    print("[ENGINE] selfplay: abort current search", flush=True)
                    try:
                        await bridge.abort_current_search()
                    except Exception:
                        pass
                    yield _sse_json({"type": "done"})
                    return
                try:
                    msg = json.loads(chunk)
                except Exception:
                    print(f"[ENGINE] selfplay: bad chunk {chunk!r}", flush=True)
                    continue
                stage = msg.get("stage")
                if stage == "searching":
                    continue
                if stage == "done":
                    bm = msg.get("bestmove")
                    print(f"[ENGINE] selfplay: bestmove={bm}", flush=True)
                    if not bm or bm == "0000":
                        print("[ENGINE] selfplay: no legal move, done", flush=True)
                        yield _sse_json({"type": "done"})
                        return
                    yield _sse_json({"type": "bestmove", "side": side_flag, "move": bm})
                    try:
                        mv = chess.Move.from_uci(bm)
                        if mv in board.legal_moves:
                            board.push(mv)
                        else:
                            print("[ENGINE] selfplay: illegal move from engine, done", flush=True)
                            yield _sse_json({"type": "done"})
                            return
                    except Exception as e:
                        print(f"[ENGINE] selfplay: push failed {e}", flush=True)
                        yield _sse_json({"type": "done"})
                        return
                    break
                if stage == "error":
                    print(f"[ENGINE] selfplay: error {msg}", flush=True)
                    yield _sse_json({"type": "info", "error": msg.get("message", "engine error")})
                    yield _sse_json({"type": "done"})
                    return

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.on_event("shutdown")
async def _shutdown():
    print("[DBG] app shutdown: stopping bridge", flush=True)
    await bridge.stop()
    print("[DBG] app shutdown: done", flush=True)