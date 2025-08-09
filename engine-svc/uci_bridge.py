# Path: engine-svc/uci_bridge.py
"""
Purpose: Manage a UCI engine process, send commands, and stream parsed `info` as JSON.

This revision:
- ADD: think_stream(...) alias to maintain backward compatibility with app.py.
- DEBUG: Small extra breadcrumbs. No behavior changes to existing methods.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import AsyncGenerator, Optional, Deque
from collections import deque

from uci_parser import parse_info_line

print("[DBG] uci_bridge loaded", flush=True)

class UciBridge:
    def __init__(self, cmd: str):
        self.cmd = cmd
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._last_lines: Deque[str] = deque(maxlen=20)
        print(f"[DBG] uci_bridge: __init__ cmd={cmd}", flush=True)

    async def _ensure_started(self):
        if self.proc and self.proc.returncode is None:
            return
        print(f"[DBG] uci_bridge: starting engine: {self.cmd}", flush=True)
        self.proc = await asyncio.create_subprocess_shell(
            self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )
        await self._send("uci\n")
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise RuntimeError("engine terminated during UCI handshake")
            txt = line.decode("utf-8", errors="replace").strip()
            self._last_lines.append(txt)
            print(f"[DBG] uci_bridge: << {txt}", flush=True)
            if txt == "uciok":
                print("[DBG] uci_bridge: handshake ok", flush=True)
                break

    async def _send(self, s: str):
        assert self.proc and self.proc.stdin
        print(f"[DBG] uci_bridge: >> {s.strip()}", flush=True)
        self.proc.stdin.write(s.encode("utf-8"))
        await self.proc.stdin.drain()

    async def isready(self) -> bool:
        await self._ensure_started()
        await self._send("isready\n")
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                return False
            txt = line.decode("utf-8", errors="replace").strip()
            self._last_lines.append(txt)
            print(f"[DBG] uci_bridge: << {txt}", flush=True)
            if txt == "readyok":
                print("[DBG] uci_bridge: isready ok", flush=True)
                return True

    async def abort_current_search(self):
        """Send UCI 'stop' and drain until 'bestmove' to leave engine clean."""
        if not self.proc or self.proc.returncode is not None:
            return
        try:
            await self._send("stop\n")
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                txt = line.decode("utf-8", errors="replace").strip()
                self._last_lines.append(txt)
                print(f"[DBG] uci_bridge: << {txt}", flush=True)
                if txt.startswith("bestmove "):
                    break
        except Exception as e:
            print(f"[DBG] uci_bridge.abort_current_search error: {e}", flush=True)

    # --------- Primary streaming method (new in our refactor) ----------
    async def stream_go(
        self,
        fen: str,
        depth: Optional[int],
        rollouts: Optional[int],
        movetime_ms: Optional[int],
    ) -> AsyncGenerator[str, None]:
        await self._ensure_started()
        assert self.proc and self.proc.stdin and self.proc.stdout

        # Set position
        if fen:
            await self._send(f"position fen {fen}\n")
        else:
            await self._send("position startpos\n")

        # Be sure engine is ready
        ok = await self.isready()
        if not ok:
            yield json.dumps({"stage": "error", "message": "engine not ready"}, separators=(",", ":"))
            return

        # Build 'go' command
        if depth is not None:
            parts = ["go", "depth", str(int(depth))]
            if rollouts is not None:
                parts += ["rollouts", str(int(rollouts))]
            go_cmd = " ".join(parts) + "\n"
        elif movetime_ms:
            go_cmd = f"go movetime {int(movetime_ms)}\n"
        else:
            yield json.dumps({"stage": "error", "message": "missing depth or movetime"}, separators=(",", ":"))
            return

        await self._send(go_cmd)

        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    rc = self.proc.returncode if self.proc else None
                    last = list(self._last_lines)[-1] if self._last_lines else ""
                    raise RuntimeError(f"engine terminated unexpectedly (code={rc}) last='{last}'")
                txt = line.decode("utf-8", errors="replace").strip()
                self._last_lines.append(txt)
                print(f"[DBG] uci_bridge: << {txt}", flush=True)

                if txt.startswith("info "):
                    info = parse_info_line(txt)
                    if info:
                        info["stage"] = "searching"
                        yield json.dumps(info, separators=(",", ":"))
                elif txt.startswith("info string "):
                    yield json.dumps({"stage": "searching", "string": txt[len("info string "):]}, separators=(",", ":"))
                elif txt.startswith("bestmove "):
                    bestmove = txt.split(" ", 1)[1].split(" ")[0]
                    yield json.dumps({"stage": "done", "bestmove": bestmove}, separators=(",", ":"))
                    break
        except (asyncio.CancelledError, GeneratorExit):
            await self.abort_current_search()
            raise
        except Exception as e:
            yield json.dumps({"stage": "error", "message": str(e)}, separators=(",", ":"))

    # --------- Back-compat alias expected by app.py ----------
    async def think_stream(
        self,
        fen: str,
        depth: Optional[int] = None,
        rollouts: Optional[int] = None,
        movetime_ms: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        print("[DBG] uci_bridge: think_stream() -> stream_go() alias", flush=True)
        async for chunk in self.stream_go(fen, depth, rollouts, movetime_ms):
            yield chunk

    async def stop(self):
        """Shutdown the engine process."""
        if self.proc and self.proc.returncode is None:
            try:
                await self._send("quit\n")
                await asyncio.sleep(0.1)
                self.proc.kill()
            except Exception:
                pass