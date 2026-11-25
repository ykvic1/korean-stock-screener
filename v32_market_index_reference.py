"""Publish compact research-only index returns already retrieved by the scanner.
No network, no raw index history, no score changes and no stale-day promotion.
"""
import json
import math
from datetime import date
from numbers import Real
from pathlib import Path

SCHEMA = 'v32-market-index-reference-1'
MARKETS = ('KOSPI', 'KOSDAQ')
PERIODS = (20, 60, 120)
SOURCE = 'FinanceDataReader KS11/KQ11 (secondary index reference)'


def build(asof, references):
    if type(asof) is not str or date.fromisoformat(asof).isoformat() != asof:
        raise ValueError('invalid market reference date')
    references = references if isinstance(references, dict) else {}
    rows = {}
    for market in MARKETS:
        ref = references.get(market)
        if not isinstance(ref, dict) or ref.get('date') != asof:
            continue
        values = {}
        for n in PERIODS:
            v = ref.get(f'r{n}')
            values[str(n)] = round(float(v), 4) if (
                isinstance(v, Real) and not isinstance(v, bool)
                and math.isfinite(v) and v > -100) else None
        if any(v is not None for v in values.values()):
            rows[market] = {'asof': asof, 'return_pct': values}
    return {'schema': SCHEMA, 'asof': asof, 'source': SOURCE,
            'official_krx_index_certified': False, 'new_provider_calls': 0,
            'markets': rows}


def write(path, asof, references):
    result = build(asof, references)
    path = Path(path)
    # Atomic replacement: readers never observe a partially written JSON file.
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                              allow_nan=False) + '\n', encoding='utf8')
    tmp.replace(path)
    return result
