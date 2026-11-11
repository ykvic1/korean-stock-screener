"""Exactly dated public FDR listing cache, for roster identity only.

The upstream FinanceDataReader KRX-MARCAP listing already consumes the same
public FinanceData/fdr_krx_data_cache daily CSV, but finds its date through an
additional KRX endpoint which can be unavailable. Select the exact requested
market day instead and keep its SHA-256 through prepare/publish.

This source is NOT an independently licensed or certified 09:00–15:30 OHLCV
feed. Do not publish raw cache rows, assert official price validation, or
substitute its total Volume for verified KRX+NXT consolidated turnover.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
import re

import pandas as pd
import requests

MAX_BYTES = 2_000_000
MIN_SOURCE_ROWS = 1000
SOURCE = ('https://raw.githubusercontent.com/FinanceData/'
          'fdr_krx_data_cache/refs/heads/master/data/listing/krx/{day}.csv')
REQUIRED = frozenset(('Code', 'Name', 'Market', 'Close', 'Volume',
                      'Amount', 'Marcap'))


class DatedRosterError(ValueError):
    """Safe error category: no upstream URL/body or raw rows in logs."""


def load_dated_listing(day: str, *, get=None, minimum=MIN_SOURCE_ROWS):
    """Return an in-memory dataframe and source digest for precisely day.

    Stale/latest generic listing fallbacks are intentionally forbidden for
    automatic publication. Once selected, the exact source digest must match
    again at merge or publication fails without score changes.
    """
    if (type(day) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day)):
        raise DatedRosterError('invalid_dated_roster_day')
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except ValueError:
        raise DatedRosterError('invalid_dated_roster_day') from None
    if type(minimum) is not int or not 1 <= minimum <= 20_000:
        raise DatedRosterError('invalid_dated_roster_minimum')
    fetch = requests.get if get is None else get
    try:
        response = fetch(SOURCE.format(day=day), timeout=(5, 20),
                         headers={'User-Agent': 'korean-stock-screener/1.0'})
        if response.status_code != 200:
            raise DatedRosterError('dated_roster_unavailable')
        raw = response.content
        if (type(raw) is not bytes or not 50 <= len(raw) <= MAX_BYTES):
            raise DatedRosterError('dated_roster_size_invalid')
    except requests.RequestException:
        raise DatedRosterError('dated_roster_unavailable') from None
    try:
        listing = pd.read_csv(BytesIO(raw), dtype={'Code': str, 'ISU_CD': str})
    except (ValueError, UnicodeError, pd.errors.ParserError):
        raise DatedRosterError('dated_roster_csv_invalid') from None
    if (len(listing) < minimum or len(listing) > 20_000
            or not REQUIRED.issubset(listing.columns)):
        raise DatedRosterError('dated_roster_schema_invalid')
    # Never silently shrink or duplicate a 6-character ticker when parsing CSV.
    keys = listing['Code'].dropna().astype(str).str.upper().str.strip()
    eligible = keys[keys.str.fullmatch(r'[0-9A-Z]{6}')]
    if len(eligible) != len(set(eligible)):
        raise DatedRosterError('dated_roster_duplicate_identity')
    return listing, sha256(raw).hexdigest()
