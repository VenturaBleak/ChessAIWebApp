# Path: game-svc/models.py
"""
Purpose: Pydantic models for API contracts between frontend and orchestrator.
Usage: Imported by app.py and orchestrator.py.
"""
from pydantic import BaseModel, Field
from typing import Literal, Optional, List

class NewGameRequest(BaseModel):
    mode: Literal['HUMAN_VS_AI', 'AI_VS_AI']

class GameStateDTO(BaseModel):
    gameId: str
    fen: str
    turn: Literal['w', 'b']
    over: bool
    result: Optional[Literal['1-0','0-1','1/2-1/2']] = None
    legalMoves: List[str]

class HumanMoveRequest(BaseModel):
    uci: str

class AiMoveRequest(BaseModel):
    movetimeMs: int = Field(ge=1, description="Time budget for this move in milliseconds")

class InsightEvent(BaseModel):
    gameId: str
    side: Literal['white', 'black']
    stage: Literal['searching', 'done', 'error']
    elapsed_ms: Optional[int] = None
    depth: Optional[int] = None
    seldepth: Optional[int] = None
    nodes: Optional[int] = None
    nps: Optional[int] = None
    score: Optional[dict] = None
    pv: Optional[List[str]] = None
    bestmove: Optional[str] = None
    message: Optional[str] = None
