"""Research-only comparison of dated Naver mobile daily closes with official KRX.

A daily Naver chart may include extended-session trading after 2026-09-14.
A matching field is only a candidate for further independent validation, never
an automatically certified 09:00-15:30 close. Never persist quote values,
official KRX source rows or issuer identities to GitHub.
"""
from __future__ import annotations

from hashlib import sha256
import math
import re

import requests

SCHEMA = 'v3-public-daily-close-candidate-crosscheck-v1'
ENDPOINT = 'https://m.stock.naver.com/api/stock/{code}/price'
SAMPLE_PER_MARKET = 12


def _price(value):
    if type(value) is int and value > 0:
        return value
    if type(value) is float and math.isfinite(value) and value > 0 and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r'[0-9][0-9,]*', value):
        number = int(value.replace(',', ''))
        return number if number > 0 else None
    return None


def parse_dated_close(payload, day):
    """Only an explicitly dated historic daily bar, not a live quote."""
    if isinstance(payload, dict):
        payload = payload.get('priceInfos')
    if not isinstance(payload, list):
        return None
    matches = [row for row in payload if isinstance(row, dict)
               and row.get('localTradedAt') == day]
    if len(matches) != 1:
        return None
    return _price(matches[0].get('closePrice'))


class NaverMobileDailyProbe:
    def __init__(self, session=None):
        self.session = session if session is not None else requests.Session()

    def daily_close(self, code, day):
        if not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code):
            return None
        try:
            result = self.session.get(
                ENDPOINT.format(code=code),
                params={'page': 1, 'pageSize': 12},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=(4, 8))
            if result.status_code != 200 or len(result.content) > 131072:
                return None
            return parse_dated_close(result.json(), day)
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
        counts = dict(sampled=len(selected), dated_candidate=0, equals_krx=0,
                      equals_saved_fchart=0, differs_from_both=0, unavailable=0)
        for code in selected:
            try:
                candidate = _price(probe.daily_close(code, day))
            except (Exception):
                candidate = None
            if candidate is None:
                counts['unavailable'] += 1
                continue
            counts['dated_candidate'] += 1
            if candidate == _price(official[(exchange, code)]):
                counts['equals_krx'] += 1
            elif candidate == _price(market[code]['price']):
                counts['equals_saved_fchart'] += 1
            else:
                counts['differs_from_both'] += 1
        if (counts['dated_candidate'] + counts['unavailable'] != counts['sampled']
            or (counts['equals_krx'] + counts['equals_saved_fchart']
                + counts['differs_from_both'] != counts['dated_candidate'])):
            raise ValueError('candidate_sample_accounting_invalid')
        stats[exchange] = counts
    return {
        'schema': SCHEMA,
        'candidate_source': 'NAVER_MOBILE_DATED_DAILY_HISTORY_UNVERIFIED_SESSION',
        'sampling_rule': 'hash_sample_from_saved_FChart_KRX_mismatches',
        'max_per_market': per_market,
        'markets': stats,
        'regular_session_source_certified': False,
        'price_or_issuer_identity_exported': False,
        'investment_score_or_coverage_promoted': False,
        'official_krx_source_data_republished': False,
    }
