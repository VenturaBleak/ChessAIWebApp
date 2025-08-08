from pydantic import BaseModel
from typing import Optional

class EngineParams(BaseModel):
    depth: int = 3
    quiescence: bool = True
    move_ordering: bool = True
    w_material: float = 1.0
    w_mobility: float = 0.1
    w_pst: float = 0.2
    w_king_safety: float = 0.1

class NewGameRequest(BaseModel):
    fen: Optional[str] = None
    ai_mode: str = "human-vs-ai"  # or "self-play"
    ai_plays: str = "black"        # "white" | "black" | "both"
    params_white: Optional[EngineParams] = None
    params_black: Optional[EngineParams] = None

class MoveRequest(BaseModel):
    gameId: str
    move: str  # UCI

class SelfPlayStepRequest(BaseModel):
    gameId: str
    steps: int = 1