"""Reviewed KRX liquidation trading windows for forward return research."""
import csv
from io import StringIO
from pathlib import Path

from v3_forward_cash_dividends import filing_day, iso_day
from v3_forward_share_actions import CODE, MARKETS, NOTICE, normalized_name

REGISTER = Path('ops/forward-reviewed-liquidation-windows.csv')
FIELDS = ('market', 'code', 'name', 'first_on', 'last_on', 'delisted_on',
          'listed_shares', 'notice_url', 'halt_on', 'halt_url')
TREATMENT = 'liquidation_trading'
OBSERVED = 'observed_liquidation_exit_proxy'


def reviewed_liquidations(raw, evidence_asof):
    reader = csv.DictReader(StringIO(raw.decode('utf-8')))
    if tuple(reader.fieldnames or ()) != FIELDS:
        raise ValueError('invalid_forward_liquidation_header')
    rows = {}
    seen = set()
    for row in reader:
        try:
            code = row['code']
            if (None in row or None in row.values() or row['market'] not in MARKETS
                    or not CODE.fullmatch(code) or not normalized_name(row['name'])
                    or not row['listed_shares'].isascii() or not row['listed_shares'].isdecimal()
                    or not 0 < int(row['listed_shares']) < 10**15
                    or not NOTICE.fullmatch(row['notice_url'])):
                raise ValueError
            first, last, delisted = (iso_day(row[field]) for field in ('first_on', 'last_on', 'delisted_on'))
            published = filing_day(row['notice_url'])
            halt, halt_source = row['halt_on'], row['halt_url']
            if (not first <= last < delisted or published > last
                    or bool(halt) != bool(halt_source)
                    or (halt and not filing_day(halt_source) <= iso_day(halt) < first)):
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError('invalid_forward_liquidation_record') from None
        if code in seen:
            raise ValueError('duplicate_forward_liquidation_record')
        seen.add(code)
        if published <= evidence_asof:
            rows[code] = {**row, 'listed_shares': int(row['listed_shares']),
                          'treatment': TREATMENT, 'start_on': halt or first}
    return rows


def load_liquidations(root, evidence_asof):
    path = root / REGISTER
    return reviewed_liquidations(path.read_bytes(), evidence_asof) if path.is_file() else {}
