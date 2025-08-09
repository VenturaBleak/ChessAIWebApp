# Path: engine-svc/uci_parser.py
"""
Purpose: Utilities to parse UCI `info` lines into structured dicts for insights.
Usage: Called by uci_bridge while streaming engine output.
"""
import shlex
from typing import Dict, List

def parse_info_line(line: str) -> Dict:
    # Example: info depth 14 seldepth 20 nodes 123456 nps 2500000 score cp 23 pv e2e4 e7e5
    parts = shlex.split(line.strip())
    out: Dict = {}
    it = iter(parts)
    for tok in it:
        if tok == 'depth':
            out['depth'] = int(next(it, '0'))
        elif tok == 'seldepth':
            out['seldepth'] = int(next(it, '0'))
        elif tok == 'nodes':
            out['nodes'] = int(next(it, '0'))
        elif tok == 'nps':
            out['nps'] = int(next(it, '0'))
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
    return out