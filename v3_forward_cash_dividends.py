"""Review cash dividend entitlements separately from confirmed payments."""
import csv
from datetime import date
from fractions import Fraction
from io import StringIO
from pathlib import Path
import re

from v3_forward_share_actions import CODE, DART_TERMS, MARKETS, NOTICE, normalized_name

REGISTER = Path('ops/forward-reviewed-cash-dividends.csv')
FIELDS = ('market', 'code', 'name', 'record_on', 'krw_per_common_share',
          'terms_url', 'scheduled_payment_on', 'paid_on', 'payment_url')


def iso_day(value):
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError('expected_iso_day')
    return value


def filing_day(url):
    if DART_TERMS.fullmatch(url):
        filed = url.rsplit('=', 1)[-1][:8]
        return iso_day(f'{filed[:4]}-{filed[4:6]}-{filed[6:]}')
    if NOTICE.fullmatch(url):
        return iso_day('-'.join(url.split('/')[4:7]))
    raise ValueError('expected_official_dividend_filing')


def reviewed_dividends(raw):
    reader = csv.DictReader(StringIO(raw.decode('utf-8')))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError('invalid_reviewed_dividend_header')
    rows = {}
    for row in reader:
        try:
            code, record = row['code'], iso_day(row['record_on'])
            terms_on = filing_day(row['terms_url'])
            scheduled = row['scheduled_payment_on']
            paid = row['paid_on']
            source = row['payment_url']
            amount = row['krw_per_common_share']
            if (None in row or None in row.values() or not CODE.fullmatch(code)
                    or row['market'] not in MARKETS or not normalized_name(row['name'])
                    or not re.fullmatch(r'\d+(?:\.\d{1,4})?', amount)
                    or Fraction(amount) <= 0
                    or (scheduled and iso_day(scheduled) < record)
                    or bool(paid) != bool(source)
                    or (paid and (iso_day(paid) < record or filing_day(source) < paid
                                  or source == row['terms_url']))):
                raise ValueError('invalid_dividend_terms')
        except (TypeError, ValueError):
            raise ValueError('invalid_reviewed_dividend_record') from None
        key = code, record
        if key in rows:
            raise ValueError('duplicate_reviewed_dividend_record')
        rows[key] = {**row, 'terms_report_on': terms_on,
                     'payment_report_on': filing_day(source) if source else None}
    return rows


def load_dividends(root, evidence_asof):
    path = root / REGISTER
    if not path.is_file():
        return {}
    rows = reviewed_dividends(path.read_bytes())
    for row in rows.values():
        if row['payment_report_on'] and row['payment_report_on'] > evidence_asof:
            row.update(paid_on='', payment_url='', payment_report_on=None)
    return rows


def dividend_sessions(sessions, dividends):
    """Map reviewed rights to the observed holding's share basis at record date."""
    if len(sessions) < 3:
        return {}
    result = {}
    for event in dividends:
        record = event['record_on']
        if sessions[2] <= record <= sessions[-1] and event['terms_report_on'] <= sessions[-1]:
            # The buy settles on the second following completed trading session.
            basis = max(day for day in sessions if day <= record)
            result.setdefault(basis, []).append(event)
    return result
