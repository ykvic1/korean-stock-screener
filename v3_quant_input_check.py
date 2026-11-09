"""Validate saved input for data-only quant updates without rerunning code tests."""
import argparse
from datetime import date
import json
import math
from pathlib import Path
import re


def checked_day(value):
    if type(value) is not str or date.fromisoformat(value).isoformat()!=value:
        raise ValueError('invalid_quant_input_date')
    return value


def validate(snapshot):
    if type(snapshot) is not dict or snapshot.get('status')!='success':
        raise ValueError('quant_input_not_successful')
    day=checked_day(snapshot.get('date'));rows=snapshot.get('stocks')
    if type(rows) is not list or not rows or len(rows)>6000:raise ValueError('invalid_quant_input_rows')
    count=snapshot.get('candidate_count')
    if type(count) is not int or count!=len(rows):raise ValueError('quant_input_count_mismatch')
    codes=set();missing_price=0;stale_price=0
    for row in rows:
        if (type(row) is not dict or type(row.get('code')) is not str
            or not re.fullmatch('[0-9A-Z]{6}',row['code']) or row['code'] in codes
            or type(row.get('name')) is not str or not row['name'].strip()
            or row.get('market') not in ('KOSPI','KOSDAQ')):raise ValueError('invalid_quant_input_identity')
        codes.add(row['code']);price=row.get('price');price_day=row.get('price_date')
        if price is None:missing_price+=1
        elif type(price) not in (int,float) or not math.isfinite(price) or price<0:
            raise ValueError('invalid_quant_input_price')
        elif price==0:missing_price+=1
        if price_day is not None:
            checked_day(price_day)
            if price_day>day:raise ValueError('quant_input_future_price')
            stale_price+=int(price_day<day)
        elif price not in (None,0):raise ValueError('quant_input_price_date_missing')
    return {'status':'valid_saved_quant_input','asof':day,'issuers':len(rows),
        'missing_or_unpriced':missing_price,'retained_older_price_rows':stale_price,
        'missing_is_not_zero':True,'provider_calls':0}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--market',type=Path,default=Path('docs/data.json'));a=p.parse_args()
    print(json.dumps(validate(json.loads(a.market.read_text())),sort_keys=True))


if __name__=='__main__':main()
