"""Guardrails for KRX day-market comparisons; never assert regular-only volume."""
from __future__ import annotations
import hashlib
import json
import re
from .clients import ProbeError, canonical_date, code6


def _numeric(raw, name, *, positive=False) -> int:
    s = str(raw).strip().replace(',', '')
    if not re.fullmatch(r'\d+', s):
        raise ProbeError('KRX field ' + name + ' missing or not an unsigned integer')
    n = int(s)
    if positive and n <= 0:
        raise ProbeError('KRX field ' + name + ' must be positive')
    return n


def source_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def inspect_krx(rows: list[dict], market: str, asof: str,
                symbols: set[str], raw_payload: bytes) -> dict:
    day = canonical_date(asof)
    if market not in ('KOSPI', 'KOSDAQ'):
        raise ValueError('Invalid KRX market')
    codes = {code6(s) for s in symbols}
    results = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ProbeError('Malformed KRX market row')
        code = str(raw.get('ISU_CD', '')).strip()
        if code not in codes:
            continue
        if code in results:
            raise ProbeError('Duplicate KRX stock row for requested symbol')
        if str(raw.get('BAS_DD', '')).replace('-', '') != day:
            raise ProbeError('KRX stock row date differs from requested trade date')
        o = _numeric(raw.get('TDD_OPNPRC'), 'TDD_OPNPRC', positive=True)
        h = _numeric(raw.get('TDD_HGPRC'), 'TDD_HGPRC', positive=True)
        l = _numeric(raw.get('TDD_LWPRC'), 'TDD_LWPRC', positive=True)
        c = _numeric(raw.get('TDD_CLSPRC'), 'TDD_CLSPRC', positive=True)
        vol = _numeric(raw.get('ACC_TRDVOL'), 'ACC_TRDVOL')
        if l > min(o, c) or h < max(o, c) or l > h:
            raise ProbeError('KRX OHLC range inconsistent for requested stock')
        results[code] = {
            'code': code, 'market': market, 'trade_date': asof,
            'ohlc_observed': {'open': o, 'high': h, 'low': l, 'close': c},
            # Important: KRX ACC_TRDVOL is not proven regular-only.
            'reported_acc_trdvol': vol,
            'regular_volume': None,
            'price_session': 'UNVERIFIED',
            'volume_session': 'UNVERIFIED',
            'official_close_status': 'unverified',
            'history_session_verified': False,
            'confirmed_scoring_allowed': False,
            'collection_status': 'observed_numeric_only',
        }
    return {
        'market': market, 'trade_date': asof,
        'requested_symbols': sorted(codes),
        'found_symbols': sorted(results),
        'missing_symbols': sorted(codes - set(results)),
        'raw_source_sha256': source_sha256(raw_payload),
        'price_session_validated': False,
        'volume_session_validated': False,
        'investor_flow_validated': False,
        'symbol_data': results,
        'purpose': 'experimental connector check; not investment-grade data',
    }


def filter_visible_filings(rows: list[dict], asof: str) -> list[dict]:
    """Conservatively exclude same-day disclosures, as in v2.2 receipt policy."""
    cutoff = canonical_date(asof)
    visible = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        filing_date = str(r.get('rcept_dt', ''))
        receipt = str(r.get('rcept_no', ''))
        if not re.fullmatch(r'\d{8}', filing_date) or not re.fullmatch(r'\d{14}', receipt):
            continue
        if filing_date < cutoff:
            visible.append({'rcept_dt': filing_date, 'rcept_no': receipt,
                            'report_nm': str(r.get('report_nm', ''))})
    return visible


def public_safety_summary(item: dict) -> dict:
    """Strip arbitrary vendor text; do not publish API rows, keys or guessed scores."""
    keys = {'code','market','trade_date','ohlc_observed','reported_acc_trdvol',
            'regular_volume','price_session','volume_session','official_close_status',
            'history_session_verified','confirmed_scoring_allowed','collection_status'}
    return {key: item[key] for key in keys if key in item}
