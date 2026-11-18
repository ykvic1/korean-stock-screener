"""Compare manually captured KRX MDCSTAT023 totals without exporting issuer data.

Use single-day period totals, the 전체 row, and the 주 and 원 units. Normalize
each capture to one CSV row per issuer, retaining its actual capture timestamp.
Run: python v3_krx_trading_snapshot_comparison.py --asof YYYY-MM-DD early.csv final.csv

Differences describe the publication stages; they do not identify trade sessions
or establish the scope of the early snapshot or the KRX OpenAPI.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import date, datetime, time
from hashlib import sha256
import io
import json
from pathlib import Path
import re

from v3_krx_regular_close import ANNOUNCED_CLOSES, KST

SOURCE = 'https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd?screenId=MDCSTAT023'
FIELDS = ('asof', 'captured_at', 'market', 'code', 'buy_volume_shares',
          'sell_volume_shares', 'buy_turnover_krw', 'sell_turnover_krw')
MARKETS = ('KOSPI', 'KOSDAQ')
EARLY = time(15, 45)
FINAL = time(20)
DIRECTIONS = ('unchanged', 'increased', 'decreased')


def quantity(value):
    if re.fullmatch(r'[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+', value or '') is None:
        raise ValueError('missing_or_invalid_total')
    return int(value.replace(',', ''))


def read_capture(raw, day, *, final):
    rows = {}
    seen_codes = set()
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError('invalid_snapshot_columns')
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('invalid_snapshot_row')
        if row['asof'] != day.isoformat():
            raise ValueError('trade_day_mismatch')
        try:
            stamp = datetime.fromisoformat(row['captured_at'])
        except ValueError:
            raise ValueError('capture_time_invalid') from None
        if stamp.utcoffset() is None:
            raise ValueError('capture_time_requires_offset')
        local = stamp.astimezone(KST)
        clock = local.time()
        expected_stage = clock >= FINAL if final else EARLY <= clock < FINAL
        if local.date() != day or not expected_stage:
            raise ValueError('outside_capture_window')
        market, code = row['market'], row['code']
        if market not in MARKETS or re.fullmatch(r'[0-9A-Z]{6}', code) is None:
            raise ValueError('invalid_market_or_code')
        key = market, code
        if code in seen_codes:
            raise ValueError('duplicate_issuer')
        seen_codes.add(code)
        buy_volume, sell_volume, buy_value, sell_value = map(quantity, (row[f] for f in FIELDS[4:]))
        if (buy_volume, buy_value) != (sell_volume, sell_value):
            raise ValueError('buy_sell_totals_do_not_balance')
        if bool(buy_volume) != bool(buy_value):
            raise ValueError('volume_value_zero_mismatch')
        # A trade has a buyer and a seller. Count one side of the overall total.
        rows[key] = buy_volume, buy_value
    if not rows:
        raise ValueError('empty_snapshot')
    return rows


def compare(early_raw, final_raw, day):
    if day.weekday() >= 5 or day in ANNOUNCED_CLOSES:
        raise ValueError('standard_trading_hours_not_applicable')
    early = read_capture(early_raw, day, final=False)
    final = read_capture(final_raw, day, final=True)
    by_market = {}
    for market in MARKETS:
        early_keys = {key for key in early if key[0] == market}
        final_keys = {key for key in final if key[0] == market}
        matched = early_keys & final_keys
        changes = {metric: Counter(dict.fromkeys(DIRECTIONS, 0))
                   for metric in ('volume', 'turnover')}
        for key in matched:
            for metric, before, after in zip(changes, early[key], final[key]):
                direction = 'increased' if after > before else 'decreased' if after < before else 'unchanged'
                changes[metric][direction] += 1
        by_market[market] = {
            'matched_count': len(matched), 'early_only_count': len(early_keys - final_keys),
            'final_only_count': len(final_keys - early_keys), 'changes': changes,
        }
    return {
        'schema': 'v3-krx-trading-snapshot-comparison-1',
        'status': 'stages_compared' if early.keys() & final.keys() else 'no_matched_issuers',
        'asof': day.isoformat(), 'source_screen': SOURCE,
        'input_sha256': {'early': sha256(early_raw).hexdigest(), 'final': sha256(final_raw).hexdigest()},
        'by_market': by_market, 'regular_only_scope_verified': False,
        'openapi_scope_verified': False, 'score_points_added': 0,
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, epilog='Normalized CSV header: ' + ','.join(FIELDS))
    parser.add_argument('--asof', required=True, type=date.fromisoformat)
    parser.add_argument('early_csv', type=Path)
    parser.add_argument('final_csv', type=Path)
    args = parser.parse_args()
    try:
        result = compare(args.early_csv.read_bytes(), args.final_csv.read_bytes(), args.asof)
    except OSError:
        parser.error('capture_file_unavailable')
    except (UnicodeError, csv.Error):
        parser.error('capture_file_must_be_utf8_csv')
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'stages_compared' else 2


if __name__ == '__main__':
    raise SystemExit(main())
