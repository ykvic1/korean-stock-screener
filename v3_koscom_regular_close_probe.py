"""Compare previously captured Koscom regular-session fields with verified KRX closes.

Input samples use the market and code from the requested closeprice route and
the original response. Output contains counts and digests only. No API calls.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
import math
from pathlib import Path
import re


MARKETS = ('KOSPI', 'KOSDAQ')
CODE = re.compile(r'[0-9A-Z]{6}')
REFERENCE_SCHEMAS = ('v3-current-corroborated-regular-closes-2',
                     'v3-current-corroborated-regular-closes-3')
MAIN_FIELDS = ('mainOpnPrc', 'mainHgPrc', 'mainLwPrc', 'mainTrdPrc',
               'mainAccTrdVol', 'mainAccTrdVal')
MAX_BYTES = 4_000_000


def check_quote(payload, market, code, day, reference):
    result = payload.get('result') if isinstance(payload, dict) else None
    if not isinstance(result, dict):
        return 'provider_result_unavailable'
    if result.get('isuSrtCd') != code:
        return 'provider_identity_mismatch'
    if any(result.get(field) is None for field in MAIN_FIELDS):
        return 'regular_fields_missing'
    traded_on = result.get('trdDd')
    if traded_on is None:
        return 'provider_trade_date_missing'
    if str(traded_on) != day.replace('-', ''):
        return 'provider_trade_date_mismatch'
    values = [result[field] for field in MAIN_FIELDS]
    if any(type(value) is not int or value < 0 for value in values):
        return 'invalid_regular_fields'
    op, high, low, close, volume, turnover = values
    if volume == turnover == 0:
        return 'no_regular_trades'
    if (volume == 0 or not 0 < low <= min(op, close) <= max(op, close) <= high
            or not low * volume <= turnover <= high * volume):
        return 'invalid_regular_fields'
    if not isinstance(reference, dict) or reference.get('market') != market:
        return 'krx_close_reference_unavailable'
    krx_close = reference.get('close')
    if type(krx_close) not in (int, float) or not 0 < krx_close < 10**12 or not math.isfinite(krx_close):
        return 'krx_close_reference_unavailable'
    if close != krx_close:
        return 'krx_close_mismatch'
    return 'regular_fields_present_close_matched'


def compare(samples_raw, reference_raw):
    samples, reference = map(json.loads, (samples_raw, reference_raw))
    if not isinstance(samples, dict) or not isinstance(reference, dict):
        raise ValueError('invalid_quote_inputs')
    day = samples.get('asof')
    if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('invalid_trade_date')
    if (reference.get('asof') != day or reference.get('schema') not in REFERENCE_SCHEMAS
            or reference.get('source') != 'PUBLIC_CLOSES_CORROBORATED_WITH_KRX'
            or not isinstance(reference.get('rows'), dict)):
        raise ValueError('invalid_or_wrong_day_krx_close_reference')
    entries = samples.get('samples')
    if not isinstance(entries, list) or not 0 < len(entries) <= 6000:
        raise ValueError('invalid_sample_count')

    seen = set()
    markets = {market: Counter() for market in MARKETS}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('invalid_market_sample')
        market, code = entry.get('market'), entry.get('code')
        if market not in MARKETS or not isinstance(code, str) or not CODE.fullmatch(code):
            raise ValueError('invalid_market_or_code')
        if code in seen:
            raise ValueError('duplicate_issuer')
        seen.add(code)
        status = check_quote(entry.get('response'), market, code, day, reference['rows'].get(code))
        markets[market][status] += 1
    return {
        'schema': 'v3-koscom-regular-close-sample-1', 'asof': day,
        'input_sha256': {'samples': sha256(samples_raw).hexdigest(),
                         'krx_closes': sha256(reference_raw).hexdigest()},
        'states_by_market': markets, 'provider_calls': 0,
        'volume_scope_certified': False, 'turnover_scope_certified': False,
        'historical_obv_verified': False, 'score_points_added': 0,
    }


def read_input(path):
    with path.open('rb') as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('input_exceeds_size_limit')
    return data


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('samples', type=Path)
    cli.add_argument('krx_closes', type=Path)
    args = cli.parse_args()
    try:
        report = compare(read_input(args.samples), read_input(args.krx_closes))
    except (OSError, UnicodeError, ValueError):
        cli.error('invalid_quote_input_or_unreadable_file')
    print(json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    main()
