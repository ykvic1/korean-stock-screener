"""Compare one date-pinned public FDR close candidate with official KRX, counts only.

KRX OpenAPI stock rows and the public FDR cache are joined ONLY IN MEMORY.
This produces no licensed prices, raw volume, or issuer identities. Exact match
does not establish permission to redistribute quotes, historical adjusted
OHLCV consistency, or all-venue consolidated traded quantities.
"""
from __future__ import annotations

import math
import numbers
import re

SCHEMA = 'v3-dated-fdr-close-versus-official-krx-aggregate-v1'
SCOPE = 'FDR_DATED_PUBLIC_CACHE_REGULAR_CLOSE_CANDIDATE_NOT_REPUBLICATION'


class CacheCorroborationError(ValueError):
    """Fixed safe error messages, never provider data."""


def _price(value):
    if type(value) is bool or value is None:
        return None
    if isinstance(value, numbers.Real):
        value = float(value)
        if math.isfinite(value) and 0 < value < 1e12:
            return value
        return None
    if isinstance(value, str) and re.fullmatch(r'[0-9][0-9,]*', value):
        numeric = int(value.replace(',', ''))
        return float(numeric) if 0 < numeric < 1e12 else None
    return None


def unavailable(reason='not_requested'):
    if reason not in ('not_requested', 'dated_cache_unavailable'):
        raise CacheCorroborationError('invalid_cache_audit_status')
    return {
        'schema': SCHEMA, 'status': reason, 'source_scope': SCOPE,
        'public_cache_source_requests': 0 if reason == 'not_requested' else 1,
        'regular_close_full_market_corroborated': False,
        'historical_ohlcv_regular_session_certified': False,
        'krx_nxt_consolidated_volume_certified': False,
        'raw_quotes_or_issuer_identities_exported': False,
        'licensed_krx_data_republished': False,
        'investment_score_or_available_weight_changed': False,
    }


def cache_close_index(frame):
    """Index public closing prices by normalized exchange and code in memory."""
    if (not hasattr(frame, 'iterrows') or not hasattr(frame, 'columns')
        or not {'Code','Market','Close'}.issubset(frame.columns)):
        raise CacheCorroborationError('untrusted_dated_cache_corroboration_inputs')
    public = {}
    for _, row in frame.iterrows():
        code, market_label = row['Code'], row['Market']
        # The FDR public cache labels the same KOSDAQ exchange cohort as
        # "KOSDAQ GLOBAL". The source-listing planner already normalizes
        # this exact designation; keep the price corroboration join coherent.
        # Never guess/convert any other unknown market or instrument class.
        market = 'KOSDAQ' if market_label == 'KOSDAQ GLOBAL' else market_label
        if (not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code)
            or market not in ('KOSPI', 'KOSDAQ')):
            continue
        key = (market, code)
        if key in public:
            raise CacheCorroborationError('duplicate_public_cache_market_code')
        public[key] = _price(row['Close'])
    return public


def compare_dated_cache(saved, official_closes, frame, source_sha):
    """Bounded, complete code+market join; no per-stock raw price output.

    official_closes contains only KRX independently validated closing prices,
    indexed by (market, 6-character code). No volume or prices are persisted.
    """
    if (not isinstance(saved, dict) or not isinstance(official_closes, dict)
        or not isinstance(source_sha, str)
        or not re.fullmatch(r'[0-9a-f]{64}', source_sha)):
        raise CacheCorroborationError('untrusted_dated_cache_corroboration_inputs')
    public = cache_close_index(frame)
    counts = {
        'requested': len(saved), 'exact_close': 0, 'different_close': 0,
        'absent_in_official': 0, 'absent_in_public_cache': 0,
        'invalid_public_cache_close': 0, 'invalid_official_close': 0,
    }
    for code, stock in saved.items():
        if (not isinstance(stock, dict) or code != stock.get('code')
            or not isinstance(code, str)
            or not re.fullmatch(r'[0-9A-Z]{6}', code)
            or stock.get('market') not in ('KOSPI', 'KOSDAQ')):
            raise CacheCorroborationError('invalid_saved_cache_corroboration_identity')
        key = (stock['market'], code)
        if key not in official_closes:
            counts['absent_in_official'] += 1
        elif _price(official_closes[key]) is None:
            counts['invalid_official_close'] += 1
        elif key not in public:
            counts['absent_in_public_cache'] += 1
        elif public[key] is None:
            counts['invalid_public_cache_close'] += 1
        elif public[key] == _price(official_closes[key]):
            counts['exact_close'] += 1
        else:
            counts['different_close'] += 1
    if (counts['requested'] < 1
        or sum(v for k,v in counts.items() if k != 'requested') != counts['requested']):
        raise CacheCorroborationError('dated_cache_corroboration_count_mismatch')
    verified = (counts['exact_close'] == counts['requested'])
    return {
        'schema': SCHEMA, 'status': 'observed',
        'source_scope': SCOPE,
        'public_cache_source_sha256': source_sha,
        'public_cache_source_requests': 1, 'counts': counts,
        'regular_close_full_market_corroborated': verified,
        'historical_ohlcv_regular_session_certified': False,
        'krx_nxt_consolidated_volume_certified': False,
        'raw_quotes_or_issuer_identities_exported': False,
        'licensed_krx_data_republished': False,
        'investment_score_or_available_weight_changed': False,
    }
