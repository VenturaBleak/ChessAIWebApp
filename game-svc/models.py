# Path: game-svc/models.py
"""
Pydantic models for API contracts between frontend and game service.
"""
from __future__ import annotations

from typing import Literal, Optional, List
from pydantic import BaseModel, Field, ConfigDict

class NewGameRequest(BaseModel):
    mode: Literal['HUMAN_VS_AI', 'AI_VS_AI', 'HUMAN_VS_HUMAN'] = 'HUMAN_VS_AI'

class MoveRequest(BaseModel):
    # Accept the frontend's {from, to, promotion?}
    from_square: str = Field(..., min_length=2, max_length=2, alias='from', description="from square (e.g., e2)")
    to_square: str = Field(..., min_length=2, max_length=2, alias='to', description="to square (e.g., e4)")
    promotion: Optional[str] = Field(None, min_length=1, max_length=1, description="promotion piece in SAN letter (q,r,b,n)")

    # Pydantic v2: use model_config, not class Config
    model_config = ConfigDict(populate_by_name=True)

class GameStateDTO(BaseModel):
    gameId: str
    fen: str
    turn: Literal['w', 'b']
    over: bool
    result: Optional[Literal['1-0','0-1','1/2-1/2']] = None
    legalMoves: List[str] = Field(default_factory=list)  # avoid mutable default