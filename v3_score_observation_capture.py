"""Record the first aligned published operational score and its actual capture time."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess

from v3_krx_regular_close import regular_close_utc
from v3_operational_research_score import OUTPUT, VERSION

ARCHIVE = Path('ops/forward-score-observations')
SCORE_PATH = 'docs/' + OUTPUT
MARKET_PATH = 'docs/data.json'
SCHEMA = 'v3-operational-score-observation-1'


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def capture(score_bytes, market_bytes, *, captured_at, source_commit, run_id, run_attempt):
    """Use the published scores as supplied, including partial and unranked rows."""
    score = json.loads(score_bytes)
    market = json.loads(market_bytes)
    day = datetime.strptime(score['asof'], '%Y-%m-%d').date()
    if (score['version'] != VERSION or score.get('investment_score_certified') is not False
            or score.get('performance_validated') is not False
            or day.isoformat() != score['asof']
            or not re.fullmatch(r'[0-9a-f]{40}', source_commit)
            or not re.fullmatch(r'[1-9][0-9]*', run_id)
            or not re.fullmatch(r'[1-9][0-9]*', run_attempt)
            or captured_at.tzinfo is None):
        raise ValueError('invalid_score_observation_source')

    market_digest = sha256(market_bytes).hexdigest()
    close = regular_close_utc(day)
    if (market['date'] != score['asof'] or score['source_hashes'].get('data.json') != market_digest
            or captured_at < close):
        return None

    stocks = {stock['code']: stock for stock in market['stocks']}
    if len(stocks) != len(market['stocks']) or len(score['rows']) != score['listed_total']:
        raise ValueError('invalid_score_observation_census')

    rows = {}
    stop_count = 0
    for code, row in sorted(score['rows'].items()):
        stock = stocks.get(code)
        reference = None
        if (stock and stock.get('name') == row['name'] and stock.get('market') == row['market']
                and stock.get('price_date') == score['asof']):
            detail = stock.get('v3_structural_stop_reference') or {}
            level = detail.get('reference_line')
            if (detail.get('asof') == score['asof'] and detail.get('unscored_reference_only') is True
                    and detail.get('score_impact') == 0 and positive(level)
                    and positive(stock.get('price')) and level < stock['price']):
                reference = level
                stop_count += 1
        rows[code] = [row['name'], row['market'], row['score'], row['rank'], row['available'],
                      row['asof'], row['oldest_asof'], reference]

    return {
        'schema': SCHEMA,
        'model_version': VERSION,
        'score_asof': score['asof'],
        'captured_at_utc': captured_at.astimezone(timezone.utc).isoformat(timespec='microseconds'),
        'regular_close_utc': close.isoformat(),
        'workflow_run_id': run_id,
        'workflow_run_attempt': run_attempt,
        'source_commit': source_commit,
        'source_score_sha256': sha256(score_bytes).hexdigest(),
        'source_market_sha256': market_digest,
        'source_price_feed': market.get('price_feed'),
        'market_regular_session_verified': market.get('price_regular_session_verified') is True,
        'score_certified': False,
        'performance_validated': False,
        'listed_count': len(rows),
        'ranked_count': sum(row[3] is not None for row in rows.values()),
        'reference_count': stop_count,
        'row_fields': ['name', 'market', 'score', 'rank', 'available', 'score_data_asof',
                       'oldest_factor_asof', 'structural_stop_reference_krw'],
        'rows': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--run-attempt', required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    score_bytes = subprocess.check_output(['git', 'show', f'{commit}:{SCORE_PATH}'])
    market_bytes = subprocess.check_output(['git', 'show', f'{commit}:{MARKET_PATH}'])
    result = capture(score_bytes, market_bytes, captured_at=datetime.now(timezone.utc),
                     source_commit=commit, run_id=args.run_id, run_attempt=args.run_attempt)
    if result is None:
        print('waiting_for_aligned_published_score')
        return

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    target = ARCHIVE / f"{result['score_asof']}.json"
    if target.exists():
        print(f"already_captured {result['score_asof']}")
        return

    payload = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    with target.open('x', encoding='utf-8') as stream:
        stream.write(payload + '\n')
    print(f"captured {result['score_asof']} {result['listed_count']} "
          f"{result['ranked_count']} {result['reference_count']}")


if __name__ == '__main__':
    main()
