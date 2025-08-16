# Path: engine-svc/uci_main.py
from __future__ import annotations
import argparse
import importlib
import sys

def _load_engine(engine_name: str):
    """
    Dynamically import engines.<name>_engine and return an instance of <NAME>Engine.
    Default is ABEngine from engines.ab_engine.
    """
    name = (engine_name or "ab").strip().lower()
    module_name = f"engines.{name}_engine"
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        # Fallback to AB if unknown
        if name != "ab":
            mod = importlib.import_module("engines.ab_engine")
        else:
            raise

    # Build class name: "ab" -> "ABEngine", "mcts" -> "MCTSEngine", etc.
    cls_name = f"{name.upper()}Engine"
    if not hasattr(mod, cls_name):
        # Fallback for AB
        cls_name = "ABEngine"
    cls = getattr(mod, cls_name)
    return cls()

def main(argv=None):
    parser = argparse.ArgumentParser(description="UCI engine loader")
    parser.add_argument("--engine", default="ab", help="Engine name (e.g., ab)")
    args = parser.parse_args(argv)

    eng = _load_engine(args.engine)
    eng.uci_loop()

if __name__ == "__main__":
    main()