"""Reviewed common-share terms for positions held through delisting."""
import csv
from fractions import Fraction
from io import StringIO
from pathlib import Path
import re

from v3_forward_cash_dividends import filing_day, iso_day
from v3_forward_share_actions import CODE, MARKETS, normalized_name

REGISTER = Path('ops/forward-reviewed-delisting-settlements.csv')
DISSOLUTION = 'dissolution_distribution'
DECIMAL = re.compile(r'\d+(?:\.\d{1,9})?')
FRACTIONAL_BASES = frozenset({'listing_close', 'listing_close_round_down',
                              'listing_close_round_half_up'})
SUCCESSOR_FIELDS = ('successor_market', 'successor_code', 'successor_name', 'share_available_on',
                    'shares_per_old_share', 'successor_shares_on_listing')
FIELDS = ('market', 'code', 'name', 'halt_on', 'halt_url', 'delisted_on',
          'delisting_url', 'treatment', 'record_on', 'terms_url', *SUCCESSOR_FIELDS,
          'cash_krw_per_old_share', 'fractional_cash_basis',
          'paid_on', 'payment_url')


def positive_decimal(value):
    return bool(DECIMAL.fullmatch(value)) and Fraction(value) > 0


def reviewed_settlements(raw):
    reader = csv.DictReader(StringIO(raw.decode('utf-8')))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError('invalid_forward_settlement_header')
    rows = {}
    for row in reader:
        try:
            if (None in row or None in row.values() or row['market'] not in MARKETS
                    or not CODE.fullmatch(row['code']) or not normalized_name(row['name'])):
                raise ValueError
            halt, delisted = iso_day(row['halt_on']), iso_day(row['delisted_on'])
            if filing_day(row['halt_url']) > halt or halt > delisted:
                raise ValueError
            evidence_on = max(filing_day(row['delisting_url']), filing_day(row['terms_url']))
            record = row['record_on']
            treatment = row['treatment']
            shares = row['shares_per_old_share']
            cash = row['cash_krw_per_old_share']
            paid, payment = row['paid_on'], row['payment_url']
            successor = tuple(row[key] for key in SUCCESSOR_FIELDS)
            fraction_basis = row['fractional_cash_basis']
            if treatment == 'review_pending':
                if any((record, *successor, cash, fraction_basis, paid, payment)):
                    raise ValueError
            elif treatment == DISSOLUTION:
                # This amount is the complete distribution for an eligible old share.
                # An explicit no-payout final report can supply zero and its completion date.
                if (any(successor) or fraction_basis
                        or (record and iso_day(record) < halt)
                        or (paid and (not record or iso_day(paid) < delisted
                                      or not DECIMAL.fullmatch(cash)))
                        or (not paid and cash)):
                    raise ValueError
            elif not halt <= iso_day(record) <= delisted:
                raise ValueError
            elif treatment == 'stock_exchange':
                listing_shares = row['successor_shares_on_listing']
                if (row['successor_market'] not in MARKETS
                        or not CODE.fullmatch(row['successor_code'])
                        or row['successor_code'] == row['code']
                        or not normalized_name(row['successor_name']) or not positive_decimal(shares)
                        or not re.fullmatch(r'[1-9][0-9]{0,14}', listing_shares)
                        or iso_day(row['share_available_on']) < record or cash
                        or (fraction_basis and fraction_basis not in FRACTIONAL_BASES)
                        or (paid and not fraction_basis)):
                    raise ValueError
            elif treatment != 'cash_exchange' or any(successor) or fraction_basis or not positive_decimal(cash):
                raise ValueError
            if bool(paid) != bool(payment) or (paid and (
                    iso_day(paid) < (row['share_available_on'] or record)
                    or payment in (row['terms_url'], row['halt_url'])
                    or filing_day(payment) < paid)):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('invalid_forward_settlement_record') from None
        key = row['halt_on'], row['code']
        if key in rows:
            raise ValueError('duplicate_forward_settlement_record')
        rows[key] = {**row, 'evidence_on': evidence_on,
                     'payment_report_on': filing_day(payment) if payment else None}
    return rows


def load_settlements(root, evidence_asof):
    path = root / REGISTER
    if not path.is_file():
        return {}
    rows = reviewed_settlements(path.read_bytes())
    available = {}
    for key, row in rows.items():
        if row['evidence_on'] > evidence_asof:
            continue
        if row['payment_report_on'] and row['payment_report_on'] > evidence_asof:
            row.update(paid_on='', payment_url='', payment_report_on=None)
            if row['treatment'] == DISSOLUTION:
                row['cash_krw_per_old_share'] = ''
        available[key] = row
    return available
