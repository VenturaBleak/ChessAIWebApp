# Path: engine-svc/uci_bridge.py
"""
Purpose: Manage a UCI engine process, send commands, and stream parsed `info` as JSON.
Usage: Imported by app.py; UciBridge.think_stream(fen, movetime_ms) yields JSON strings suitable for SSE.
"""
import asyncio
import json
import os
import sys
from typing import AsyncGenerator, Optional
from uci_parser import parse_info_line

class UciBridge:
    def __init__(self, cmd: str):
        self.cmd = cmd
        self.proc: Optional[asyncio.Process] = None
        self.lock = asyncio.Lock()

    async def start(self):
        if self.proc and self.proc.returncode is None:
            return
        self.proc = await asyncio.create_subprocess_shell(
            self.cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._send("uci\n")
        await self._await_keyword("uciok")

    async def _send(self, s: str):
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(s.encode())
        await self.proc.stdin.drain()

    async def _await_keyword(self, kw: str):
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise RuntimeError("Engine exited")
            txt = line.decode(errors='ignore').strip()
            if txt == kw:
                return

    async def ready(self, timeout_ms: int = 3000) -> bool:
        await self.start()
        await self._send("isready\n")
        try:
            await asyncio.wait_for(self._await_keyword("readyok"), timeout_ms / 1000)
            return True
        except asyncio.TimeoutError:
            return False

    async def new_game(self):
        await self.start()
        await self._send("ucinewgame\n")
        await self.ready()

    async def think_stream(self, fen: str, movetime_ms: int) -> AsyncGenerator[str, None]:
        async with self.lock:
            await self.start()
            await self._send(f"position fen {fen}\n")
            await self._send("isready\n")
            await self._await_keyword("readyok")

            # Start search
            await self._send(f"go movetime {movetime_ms}\n")

            bestmove: Optional[str] = None
            assert self.proc and self.proc.stdout
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                txt = line.decode(errors='ignore').strip()
                if txt.startswith('info '):
                    info = parse_info_line(txt[5:])
                    if info:
                        yield json.dumps(info, separators=(',',':'))
                elif txt.startswith('bestmove '):
                    bestmove = txt.split(' ', 1)[1].split(' ')[0]
                    yield json.dumps({"stage":"done","bestmove":bestmove}, separators=(',',':'))
                    break

    async def stop(self):
        if self.proc and self.proc.returncode is None:
            try:
                await self._send("quit\n")
                await asyncio.sleep(0.1)
                self.proc.kill()
            except Exception:
                pass