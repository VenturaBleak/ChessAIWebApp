# Path: engine-svc/uci_parser.py
"""
Parse UCI `info` lines into structured dicts for the UI.
"""
import shlex
from typing import Dict

print("[DBG] uci_parser loaded", flush=True)

def parse_info_line(line: str) -> Dict:
    s = line.strip()
    try:
        print(f"[DBG] uci_parser.parse_info_line input: {s}", flush=True)
    except Exception:
        pass
    parts = shlex.split(s)
    out: Dict = {}
    it = iter(parts)
    for tok in it:
        if tok == 'info':
            continue
        elif tok == 'depth':
            out['depth'] = int(next(it, '0'))
        elif tok == 'nodes':
            out['nodes'] = int(next(it, '0'))
        elif tok == 'nps':
            out['nps'] = int(next(it, '0'))
        elif tok == 'hashfull':
            out['hashfull'] = int(next(it, '0'))
        elif tok == 'score':
            kind = next(it, '')
            val = next(it, '0')
            if kind == 'cp':
                out['score'] = {'cp': int(val)}
            elif kind == 'mate':
                out['score'] = {'mate': int(val)}
        elif tok == 'pv':
            out['pv'] = list(it)
            break
        elif tok == 'string':
            out['string'] = " ".join(list(it))
            break
    try:
        print(f"[DBG] uci_parser.parse_info_line output: {out}", flush=True)
    except Exception:
        pass
    return out
