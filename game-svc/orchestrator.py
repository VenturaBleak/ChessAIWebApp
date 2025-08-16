# Path: game-svc/orchestrator.py
"""
Game-state orchestration only (no engine calls).

- In-memory GameStore
- Human/engine-proposed move application + legality via python-chess
- Full end-state detection (mate/stalemate/insufficient material/75-move/repetition)
- Deterministic legalMoves listing (UCI)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import chess

@dataclass
class Game:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)  # auto-generate on init
    mode: str = "HUMAN_VS_HUMAN"
    board: chess.Board = field(default_factory=chess.Board)

    def legal_moves_uci(self):
        return sorted(m.uci() for m in self.board.legal_moves)

    def result_str(self):
        if not self.board.is_game_over(claim_draw=True):
            return None
        return self.board.result(claim_draw=True)

    def state(self):
        return {
            "gameId": self.id,
            "fen": self.board.fen(),
            "turn": "w" if self.board.turn == chess.WHITE else "b",
            "over": self.board.is_game_over(claim_draw=True),
            "result": self.result_str(),
            "legalMoves": self.legal_moves_uci(),
        }

class GameStore:
    def __init__(self) -> None:
        self._games = {}

    def new(self, mode: str) -> Game:
        g = Game(mode=mode)  # id auto-created
        self._games[g.id] = g
        return g

    def get(self, gid: str) -> Game:
        g = self._games.get(gid)
        if not g:
            raise KeyError(gid)
        return g

STORE = GameStore()

# ————— Move application helpers —————
PROMO_MAP = {'q':'q','r':'r','b':'b','n':'n'}

# game-svc/orchestrator.py
def apply_move(g: Game, from_sq: str, to_sq: str, promotion: Optional[str] = None) -> Game:
    if promotion:
        p = promotion.lower()
        if p not in PROMO_MAP:
            raise ValueError(f"invalid promotion piece: {promotion}")
        uci = (from_sq + to_sq + p).lower()
    else:
        uci = (from_sq + to_sq).lower()

    try:
        move = chess.Move.from_uci(uci)
    except Exception:
        raise ValueError(f"invalid move format: {uci}")

    if move not in g.board.legal_moves:
        raise ValueError(f"illegal move: {uci}")

    g.board.push(move)
    return g