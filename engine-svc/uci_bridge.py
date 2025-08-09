# Path: engine-svc/uci_bridge.py
"""
Purpose: Manage a UCI engine process, send commands, and stream parsed `info` as JSON.

This hardened build fixes self-play stalls caused by concurrent reads:
- All reads from engine stdout are serialized with a single asyncio.Lock.
- abort_current_search(): send one STOP; if another reader is active, don't drain.
- Preflight STOP before new search; isready() has timeout + auto-restart.
- Extra breadcrumbs preserved.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncGenerator, Optional, Deque
from collections import deque

from uci_parser import parse_info_line

PRINT_DBG = True
def _dbg(msg: str):
    if PRINT_DBG:
        print(f"[DBG] uci_bridge: {msg}", flush=True)

print("[DBG] uci_bridge loaded", flush=True)

class UciBridge:
    def __init__(self, cmd: str):
        self.cmd = cmd
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._last_lines: Deque[str] = deque(maxlen=50)
        self._read_lock = asyncio.Lock()           # NEW: serialize all stdout reads
        self._search_active = False                # NEW: track active search
        self._last_stop_ts = 0.0                   # NEW: throttle STOP
        _dbg(f"__init__ cmd={cmd}")

    # ---------------- core process mgmt ----------------
    async def _spawn(self):
        _dbg(f"starting engine: {self.cmd}")
        self.proc = await asyncio.create_subprocess_shell(
            self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=os.environ.copy(),
        )

    async def _ensure_started(self):
        if self.proc and self.proc.returncode is None:
            return
        await self._spawn()
        await self._send("uci\n")
        try:
            # handshake under read lock
            async with self._read_lock:
                while True:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=3.0)  # type: ignore[arg-type]
                    if not line:
                        raise RuntimeError("engine terminated during UCI handshake")
                    txt = line.decode("utf-8", errors="replace").strip()
                    self._last_lines.append(txt)
                    _dbg(f"<< {txt}")
                    if txt == "uciok":
                        _dbg("handshake ok")
                        break
        except asyncio.TimeoutError:
            raise RuntimeError("uci handshake timed out")

    async def _restart_engine(self):
        _dbg("restarting engine process")
        if self.proc:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        await self._ensure_started()

    # ---------------- i/o helpers ----------------
    async def _send(self, s: str):
        assert self.proc and self.proc.stdin
        _dbg(f">> {s.strip()}")
        self.proc.stdin.write(s.encode("utf-8"))
        await self.proc.stdin.drain()

    async def _readline_timeout(self, timeout: float) -> Optional[str]:
        """
        Read one line with timeout.
        Returns:
          None  -> timed out
          ""    -> stream closed
          "..." -> line
        Always serialized via _read_lock to avoid concurrent reads.
        """
        assert self.proc and self.proc.stdout
        try:
            async with self._read_lock:
                try:
                    line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=timeout)  # type: ignore[arg-type]
                except asyncio.TimeoutError:
                    return None
        except RuntimeError as e:
            # Shouldn't happen with the lock, but guard anyway
            _dbg(f"_readline_timeout lock error: {e}")
            return None

        if not line:
            return ""
        txt = line.decode("utf-8", errors="replace").strip()
        self._last_lines.append(txt)
        _dbg(f"<< {txt}")
        return txt

    # ---------------- public ops ----------------
    async def isready(self, restart_on_timeout: bool = True) -> bool:
        await self._ensure_started()
        await self._send("isready\n")

        # Wait up to 2s; if no 'readyok', restart once.
        for attempt in (1, 2):
            deadline = asyncio.get_event_loop().time() + 2.0
            while asyncio.get_event_loop().time() < deadline:
                txt = await self._readline_timeout(0.25)
                if txt is None:
                    continue  # timeout slice; keep waiting
                if txt == "":
                    return False  # engine died
                if txt == "readyok":
                    _dbg("isready ok")
                    return True
                # ignore other lines (info, etc.)
            if not restart_on_timeout or attempt == 2:
                break
            _dbg("isready timeout — restarting engine")
            await self._restart_engine()
            await self._send("isready\n")
        return False

    async def abort_current_search(self):
        """
        Send 'stop' and (if safe) drain briefly until 'bestmove'.
        If another coroutine is currently reading (lock held), we *only* send stop
        and let the main reader consume the remainder — avoids concurrent read errors.
        """
        if not self.proc or self.proc.returncode is not None:
            return

        # Throttle duplicate STOPs within 100ms
        now = time.monotonic()
        if now - self._last_stop_ts < 0.1:
            _dbg("skip STOP (throttled)")
            return
        self._last_stop_ts = now

        try:
            await self._send("stop\n")
        except Exception as e:
            _dbg(f"abort_current_search send error: {e}")
            return

        # If a search loop is active and holding the read lock, don't drain here.
        if self._read_lock.locked() or self._search_active:
            _dbg("abort_current_search: reader active; not draining")
            return

        # Reader seems idle — drain quickly under the lock
        _dbg("abort_current_search: draining")
        deadline = asyncio.get_event_loop().time() + 0.8
        while asyncio.get_event_loop().time() < deadline:
            txt = await self._readline_timeout(0.1)
            if txt is None:
                continue
            if not txt:
                break
            if txt.startswith("bestmove "):
                break

    async def _preflight_reset(self):
        """Ensure engine is idle before new 'position'/'go'. Safe even if already idle."""
        try:
            await self.abort_current_search()
        except Exception as e:
            _dbg(f"preflight abort error: {e}")

    # --------- Primary streaming method ----------
    async def stream_go(
        self,
        fen: str,
        depth: Optional[int],
        rollouts: Optional[int],
        movetime_ms: Optional[int],
    ) -> AsyncGenerator[str, None]:
        await self._ensure_started()
        await self._preflight_reset()

        assert self.proc and self.proc.stdin and self.proc.stdout

        # Set position
        if fen:
            await self._send(f"position fen {fen}\n")
        else:
            await self._send("position startpos\n")

        # Be sure engine is ready (with restart on timeout)
        ok = await self.isready(restart_on_timeout=True)
        if not ok:
            yield json.dumps({"stage": "error", "message": "engine not ready"}, separators=(",", ":"))
            return

        # Build 'go'
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

        # Read loop
        self._search_active = True
        try:
            while True:
                txt = await self._readline_timeout(5.0)
                if txt is None:
                    continue  # keep waiting
                if txt == "":
                    rc = self.proc.returncode if self.proc else None
                    last = list(self._last_lines)[-1] if self._last_lines else ""
                    raise RuntimeError(f"engine terminated unexpectedly (code={rc}) last='{last}'")
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
        finally:
            self._search_active = False

    # Back-compat alias expected by app.py
    async def think_stream(
        self,
        fen: str,
        depth: Optional[int] = None,
        rollouts: Optional[int] = None,
        movetime_ms: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        _dbg("think_stream() -> stream_go() alias")
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