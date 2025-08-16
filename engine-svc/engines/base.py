# Path: engine-svc/engines/base.py
from __future__ import annotations
import sys
from abc import ABC, abstractmethod

# Keep default ID lines EXACTLY as before to preserve UCI handshake bytes
_DEFAULT_ID_NAME = "PyRefEngine (AB-only)"
_DEFAULT_ID_AUTHOR = "open-source"

class Engine(ABC):
    """
    Base UCI Engine.

    Subclasses must implement:
      - handle_position_cmd(cmd)
      - go(cmd) -> str
      - bestmove_now() -> str
      - on_new_game() (optional)
      - on_quit() (optional)

    This base implements the shared UCI loop and preserves all prints.
    """

    # ---- Hooks / metadata (override if needed) ----
    def engine_name(self) -> str:
        return _DEFAULT_ID_NAME

    def engine_author(self) -> str:
        return _DEFAULT_ID_AUTHOR

    # ---- Lifecycle (optional overrides) ----
    def on_new_game(self) -> None:
        pass

    def on_quit(self) -> None:
        pass

    # ---- Abstract engine ops ----
    @abstractmethod
    def handle_position_cmd(self, cmd: str) -> None:
        """Handle a full `position ...` line."""
        raise NotImplementedError

    @abstractmethod
    def go(self, cmd: str) -> str:
        """
        Handle a full `go ...` line and return the bestmove in UCI.
        Must print identical `info ...` lines as the legacy engine did.
        """
        raise NotImplementedError

    @abstractmethod
    def bestmove_now(self) -> str:
        """Return an immediate best move (used for `stop`)."""
        raise NotImplementedError

    # ---- Shared UCI loop (unchanged behavior) ----
    def _print_uci_id(self) -> None:
        print(f"id name {self.engine_name()}")
        print(f"id author {self.engine_author()}")
        print("uciok")
        sys.stdout.flush()

    def uci_loop(self) -> None:
        # Initial handshake
        self._print_uci_id()
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            cmd = line.strip()

            # Preserve debug breadcrumb exactly
            print(f"info string dbg=recv '{cmd}'", flush=True)

            if cmd == "isready":
                print("readyok")
                sys.stdout.flush()

            elif cmd == "uci":
                self._print_uci_id()

            elif cmd.startswith("ucinewgame"):
                self.on_new_game()

            elif cmd.startswith("position "):
                self.handle_position_cmd(cmd)

            elif cmd.startswith("go "):
                best_uci = self.go(cmd)
                print(f"bestmove {best_uci}")
                sys.stdout.flush()

            elif cmd == "stop":
                print(f"bestmove {self.bestmove_now()}")
                sys.stdout.flush()

            elif cmd == "quit":
                print("info string dbg=quit", flush=True)
                try:
                    self.on_quit()
                finally:
                    break