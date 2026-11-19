"""Compare dated Npay regular and final closes with KRX and saved FChart."""
from __future__ import annotations

from hashlib import sha256
import math
import re

import requests

SCHEMA = 'v3-public-daily-close-candidate-crosscheck-v2'
ENDPOINT = 'https://stock.naver.com/api/stockSecurity/items/v2/domestic/{code}/daily-prices'
SAMPLE_PER_MARKET = 12
STATES = ('regular_matches_krx_and_final_matches_saved', 'regular_matches_krx_only',
          'final_matches_saved_only', 'neither_matches', 'unavailable')


def _price(value):
    if type(value) is int and value > 0:
        return value
    if type(value) is float and math.isfinite(value) and value > 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r'[0-9][0-9,]*', value):
        number = int(value.replace(',', ''))
        return number if number > 0 else None
    return None


def parse_dated_prices(payload, day):
    """Require both prices from one uniquely dated response row."""
    rows = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    matches = [row for row in rows if isinstance(row, dict)
               and row.get('tradingDateKst') == day]
    if len(matches) != 1:
        return None
    regular = _price(matches[0].get('regularMarketClosingPrice'))
    final = _price(matches[0].get('closingPrice'))
    if regular is None or final is None:
        return None
    return regular, final


class NpayDailyPricesProbe:
    def __init__(self, session=None):
        self.session = session if session is not None else requests.Session()

    def daily_prices(self, code, day):
        if not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code):
            return None
        try:
            result = self.session.get(
                ENDPOINT.format(code=code),
                params={'size': 12},
                headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                         'Referer': 'https://stock.naver.com/'},
                timeout=(3, 5))
            if result.status_code != 200 or len(result.content) > 131072:
                return None
            return parse_dated_prices(result.json(), day)
        except (requests.RequestException, ValueError, TypeError, OverflowError):
            return None


def compare_candidate(market, official, day, probe, *, per_market=SAMPLE_PER_MARKET):
    """Return only bounded aggregate counts for saved-vs-KRX mismatches.

    Choose stable hash-distributed issuer samples, not only low numbered stocks.
    The provider is intentionally separate from the source of official truth.
    """
    if not isinstance(per_market, int) or not 1 <= per_market <= 30:
        raise ValueError('invalid_candidate_sample_bound')
    stats = {}
    for exchange in ('KOSPI', 'KOSDAQ'):
        eligible = []
        for code, row in market.items():
            if row.get('market') != exchange or row.get('price_date') != day:
                continue
            saved = _price(row.get('price'))
            krx = _price(official.get((exchange, code)))
            if saved is not None and krx is not None and saved != krx:
                eligible.append(code)
        eligible.sort(key=lambda code: sha256((day + ':' + code).encode()).digest())
        selected = eligible[:per_market]
        counts = dict.fromkeys(STATES, 0)
        for code in selected:
            prices = probe.daily_prices(code, day)
            if prices is None:
                counts['unavailable'] += 1
                continue
            regular, final = prices
            regular_matches = regular == _price(official[(exchange, code)])
            final_matches = final == _price(market[code]['price'])
            if regular_matches and final_matches:
                counts['regular_matches_krx_and_final_matches_saved'] += 1
            elif regular_matches:
                counts['regular_matches_krx_only'] += 1
            elif final_matches:
                counts['final_matches_saved_only'] += 1
            else:
                counts['neither_matches'] += 1
        stats[exchange] = {'sampled': len(selected), **counts}
    return {
        'schema': SCHEMA,
        'candidate_source': 'NPAY_DATED_REGULAR_AND_FINAL_CLOSE_FIELDS',
        'sampling_rule': 'hash_sample_from_saved_FChart_KRX_mismatches',
        'max_per_market': per_market,
        'markets': stats,
        'regular_session_source_certified': False,
        'price_or_issuer_identity_exported': False,
        'investment_score_or_coverage_promoted': False,
        'official_krx_source_data_republished': False,
    }
