# Path: game-svc/orchestrator.py
"""
Purpose: Game state management and orchestration utilities.
Usage: Imported by app.py; contains GameStore (in-memory), Game model, and helper calls to engine-svc.
Surgical change: ensure we only apply the engine's move WHEN AND ONLY WHEN the engine signals stage "done"
(i.e., after it has fully used its movetime). We do not pick a move early from in-flight PVs.
We also avoid any random fallbacks; if the engine fails to return a bestmove, we propagate an error.
"""
from __future__ import annotations
import asyncio
import uuid
from typing import Dict, Optional, AsyncGenerator, Tuple

import httpx
import chess

ENGINE_SVC_URL = "http://engine-svc:8001"

class Game:
    def __init__(self, gid: str):
        self.id = gid
        self.board = chess.Board()
        self.over: bool = False
        self.result: Optional[str] = None

    def state(self) -> Dict:
        return {
            "gameId": self.id,
            "fen": self.board.fen(),
            "turn": 'w' if self.board.turn else 'b',
            "over": self.over,
            "result": self.result,
            "legalMoves": [m.uci() for m in self.board.legal_moves],
        }

class GameStore:
    def __init__(self):
        self._games: Dict[str, Game] = {}
        self._lock = asyncio.Lock()

    def get(self, gid: str) -> Optional[Game]:
        return self._games.get(gid)

    def create(self) -> Game:
        gid = uuid.uuid4().hex[:8]
        g = Game(gid)
        self._games[gid] = g
        return g

store = GameStore()

async def _engine_think_stream(fen: str, movetime_ms: int) -> AsyncGenerator[str, None]:
    """
    Pipe-through to engine-svc /uci/think SSE.
    Yields compact JSON strings (already serialized by engine-svc).
    IMPORTANT: We do not impose a client-side timeout; the engine controls timing.
    """
    url = f"{ENGINE_SVC_URL}/uci/think?movetimeMs={movetime_ms}"
    # No timeout to avoid premature cutoffs; engine will finish on its own.
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(url, json={"fen": fen}, headers={"Accept": "text/event-stream"})
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line:
                continue
            if line.startswith("data: "):
                yield line[len("data: "):]

async def apply_ai_move(game: Game, side: str, movetime_ms: int) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
    """
    Start the engine for the given FEN and movetime.
    Stream through the engine's JSON chunks to the caller (SSE).
    When 'done' is received, apply the bestmove to the board if legal.

    Yields (chunk_json, bestmove_when_done_or_None)
    """
    if game.over:
        # announce terminal state once
        yield '{"stage":"done","message":"game over"}', None
        return

    # Enforce correct side to move
    expected_side = 'white' if game.board.turn else 'black'
    if side != expected_side:
        # No move; just a notice
        yield f'{{"stage":"error","message":"side mismatch: expected {expected_side}"}}', None
        return

    final_best: Optional[str] = None
    # Relay engine stream verbatim; do not finalize early.
    async for chunk in _engine_think_stream(game.board.fen(), movetime_ms):
        # Forward every chunk to the caller
        yield chunk, None
        # Only commit when engine explicitly finishes
        # NOTE: we parse minimally to avoid coupling; look for small sentinel substrings
        if '"stage":"done"' in chunk and '"bestmove":"' in chunk:
            try:
                # Extract "bestmove":"...." without full JSON parse for perf
                # Fallback to JSON parse if pattern search fails
                start = chunk.find('"bestmove":"')
                if start != -1:
                    start += len('"bestmove":"')
                    end = chunk.find('"', start)
                    bestmove = chunk[start:end] if end != -1 else None
                else:
                    import json as _json
                    bestmove = _json.loads(chunk).get("bestmove")
            except Exception:
                bestmove = None

            if bestmove:
                mv = chess.Move.from_uci(bestmove)
                if mv in game.board.legal_moves:
                    game.board.push(mv)
                    if game.board.is_game_over():
                        game.over = True
                        game.result = game.board.result(claim_draw=True)
                    final_best = bestmove
                else:
                    # Illegal from engine? Don't apply; surface as error to client.
                    yield f'{{"stage":"error","message":"illegal bestmove {bestmove}"}}', None
            break

    if final_best:
        # Tell the client we applied it (distinct stage for the UI, then they may refetch state)
        yield '{{"stage":"applied","bestmove":"{bm}"}}'.format(bm=final_best), final_best
    else:
        # If engine didn't return a bestmove, don't choose randomly.
        # Let the caller decide how to handle it.
        yield '{"stage":"error","message":"engine returned no bestmove"}', None

async def selfplay_loop(game: Game, white_ms: int, black_ms: int, send):
    """Continuous self-play loop; alternates turns until game over or cancelled."""
    try:
        while not game.over:
            side = 'white' if game.board.turn else 'black'
            movetime = white_ms if side == 'white' else black_ms
            async for chunk, _ in apply_ai_move(game, side, movetime):
                await send(chunk)
            await asyncio.sleep(0)  # cooperative yield
    except asyncio.CancelledError:
        pass