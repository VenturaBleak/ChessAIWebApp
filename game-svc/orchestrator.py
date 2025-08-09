# Path: game-svc/orchestrator.py
"""
Purpose: Game state management and orchestration utilities.
Usage: Imported by app.py; contains GameStore (in-memory) and helper calls to engine-svc.
"""
from __future__ import annotations
import asyncio
import uuid
from typing import Dict, Optional, AsyncGenerator, Tuple

import httpx
import chess

ENGINE_SVC_URL = "http://engine-svc:8001"

class Game:
    def __init__(self, game_id: str):
        self.id = game_id
        self.board = chess.Board()
        self.over: bool = False
        self.result: Optional[str] = None  # '1-0' | '0-1' | '1/2-1/2'
        # selfplay task handle
        self._selfplay_task: Optional[asyncio.Task] = None

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
        self.games: Dict[str, Game] = {}

    def create(self) -> Game:
        gid = str(uuid.uuid4())
        g = Game(gid)
        self.games[gid] = g
        return g

    def get(self, gid: str) -> Optional[Game]:
        # DO NOT raise on missing; callers should handle None
        return self.games.get(gid)

store = GameStore()

async def engine_think_stream(fen: str, movetime_ms: int) -> AsyncGenerator[str, None]:
    """Proxy to engine-svc /uci/think SSE and yield raw JSON strings from that stream."""
    params = {"movetimeMs": movetime_ms}
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        async with client.stream("POST", f"{ENGINE_SVC_URL}/uci/think", params=params, json={"fen": fen}) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                # The engine sends plain JSON lines (not prefixed with "data:")
                yield line

async def apply_ai_move(game: Game, side: str, movetime_ms: int) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
    """
    Ask engine-svc to think from the game's current FEN for movetime_ms milliseconds.
    Stream raw JSON chunks (as SSE 'data:' lines expected by the frontend) to the caller.
    When 'done' is received, apply the bestmove to the board if legal.
    Yields (chunk_json, bestmove_when_done_or_None)
    """
    final_best: Optional[str] = None
    async for chunk in engine_think_stream(game.board.fen(), movetime_ms):
        # Pass-through chunk to SSE
        try:
            # detect final
            if '"stage":"done"' in chunk and '"bestmove":"' in chunk:
                # cheap parse
                import json
                payload = json.loads(chunk)
                final_best = payload.get("bestmove")
        except Exception:
            pass
        yield chunk, None

    # After stream ends, if we saw a final best move, try to apply it
    if final_best:
        try:
            mv = chess.Move.from_uci(final_best)
            if mv in game.board.legal_moves:
                game.board.push(mv)
                if game.board.is_game_over():
                    game.over = True
                    game.result = game.board.result(claim_draw=True)
            # else ignore illegal (engine desync)
        except Exception:
            pass
        yield '{"stage":"applied","bestmove":"%s"}' % final_best, final_best

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
