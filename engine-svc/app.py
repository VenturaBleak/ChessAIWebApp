# Path: engine-svc/app.py
"""
FastAPI service that wraps a UCI engine process and exposes:
- GET /health: liveness
- GET /uci/ready: check the engine responds to "isready"
- POST /uci/think?movetimeMs=XXXX with JSON { "fen": "<FEN>" }:
  streams Server-Sent Events ("text/event-stream") with incremental search info
"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import asyncio
import os
import json
from uci_bridge import UciBridge

app = FastAPI(title="engine-svc", version="1.0.0")

ENGINE_CMD = os.environ.get("UCI_ENGINE_CMD", "python /app/uci_reference_engine.py")
bridge = UciBridge(ENGINE_CMD)

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/uci/ready")
async def uci_ready():
    ok = await bridge.ensure_ready()
    return {"ok": ok}

@app.post("/uci/think")
async def uci_think(body: dict, movetimeMs: int):
    fen = body.get("fen")
    if not fen:
        raise HTTPException(400, "Missing FEN")

    async def gen():
        try:
            async for chunk in bridge.think_stream(fen, movetimeMs):
                # Each chunk is a compact JSON string; emit as an SSE "data:" line
                yield f"data: {chunk}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'stage':'error','message':str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

@app.on_event("shutdown")
async def _shutdown():
    await bridge.stop()