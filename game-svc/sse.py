# Path: game-svc/sse.py
"""
Purpose: Utilities to send Server-Sent Events from FastAPI endpoints.
Usage: from sse import sse_event, sse_json
"""
import json
from typing import Dict

def sse_event(event: Dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"

def sse_json(chunk: str) -> str:
    # Assumes chunk is already a JSON string (from engine-svc)
    return f"data: {chunk}\n\n"