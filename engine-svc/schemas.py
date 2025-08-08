from typing import Optional, Annotated
from pydantic import BaseModel, Field, conint, confloat

# Constrained types (v2 style)
Depth = conint(ge=1, le=8)        # MVP: cap to 8 for snappy UX
Weight = confloat(ge=0.0, le=5.0) # sane-ish upper bound for weights

class EngineParams(BaseModel):
    depth: int = 3
    quiescence: bool = True
    move_ordering: bool = True
    w_material: float = 1.0
    w_mobility: float = 0.1
    w_pst: float = 0.2
    w_king_safety: float = 0.1
    max_time_ms: int = 1500
    max_nodes: int = 200000

    model_config = {"extra": "forbid",
        "json_schema_extra": {"description": "Search depth and evaluation weights for the engine."}
    }

class BestMoveRequest(BaseModel):
    fen: str = Field(..., description="FEN of the current position")
    params: Optional[EngineParams] = None

    model_config = {"extra": "forbid"}

class EvaluateRequest(BaseModel):
    fen: str = Field(..., description="FEN of the current position")
    params: Optional[EngineParams] = None

    model_config = {"extra": "forbid"}