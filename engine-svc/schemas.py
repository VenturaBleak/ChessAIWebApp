from pydantic import BaseModel
from typing import Optional

class EngineParams(BaseModel):
    depth: int = 3
    quiescence: bool = True
    move_ordering: bool = True
    # eval weights
    w_material: float = 1.0
    w_mobility: float = 0.1
    w_pst: float = 0.2
    w_king_safety: float = 0.1

class BestMoveRequest(BaseModel):
    fen: str
    params: Optional[EngineParams] = None

class EvaluateRequest(BaseModel):
    fen: str
    params: Optional[EngineParams] = None