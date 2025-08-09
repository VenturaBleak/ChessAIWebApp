# Path: engine-svc/uci_parser.py
"""
Purpose: Utilities to parse UCI `info` lines into structured dicts for insights.
Usage: Called by uci_bridge while streaming engine output.
"""
import shlex
from typing import Dict, List

def parse_info_line(line: str) -> Dict:
    # Examples:
    #   info depth 14 nodes 123456 nps 2500000 score cp 23 pv e2e4 e7e5
    #   info string dbg=done elapsed=1.999s nodes=123456
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
        elif tok == 'string':
            # The rest of the tokens form a free-form message
            out['string'] = ' '.join(list(it))
            break
    return out