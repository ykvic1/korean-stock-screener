"""Apply 16 already-collected, same-close observations; no provider calls."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from v3_pattern_composite_score import _observed, compute_candidates

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--verify', action='store_true')
args = p.parse_args()
saved = json.loads(Path(__file__).with_name('chart-recovery-20261109.json').read_text())
target = ROOT/'docs/data.json'
raw = target.read_bytes()
if sha256(raw).hexdigest() == saved['target_market_sha256']:
    print('Saved recovery already applied; no collection or overwrite.')
    sys.exit(0)
if sha256(raw).hexdigest() != saved['source_market_sha256']:
    raise ValueError('market_source_changed_review_required')
market = json.loads(raw)
if market['date'] != '2026-11-09':
    raise ValueError('recovery_market_date_mismatch')
stocks = {r['code']: r for r in market['stocks']}
for code, update in saved['updates'].items():
    row = stocks[code]
    if (row['price_date'], row['price']) != (update['price_date'], update['price']):
        raise ValueError('recovery_close_or_date_mismatch')
    for field, observation in update['observations'].items():
        if field not in ('pattern_research', 'pattern_weekly_research'):
            raise ValueError('unexpected_recovery_field')
        frame = 'daily' if field == 'pattern_research' else 'weekly'
        trial = dict(row)
        trial[field] = observation
        if _observed(trial, frame) is None:
            raise ValueError('unverified_chart_observation')
        row[field] = observation
compute_candidates(market['stocks'])
status = market['v3_technical_pattern_status']
status['technical_observed_25_count'] = sum(r['v3_pattern_integrated']['technical_research_candidate'] is not None for r in market['stocks'])
status['candidate_full_six_pillar_count'] = sum(r['v3_pattern_integrated']['six_pillar_full_candidate_only'] is not None for r in market['stocks'])
market['v3_targeted_price_recovery'] = saved['metadata']
encoded = (json.dumps(market, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode()
if sha256(encoded).hexdigest() != saved['target_market_sha256']:
    raise ValueError('recovery_output_does_not_match_reviewed_snapshot')
if not args.verify:
    temporary = target.with_suffix('.recovery.tmp')
    temporary.write_bytes(encoded)
    temporary.replace(target)
    receipt = ROOT/'docs/v3-targeted-price-recovery-20261109.json'
    receipt.write_text(json.dumps(saved['receipt'],ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')
print(json.dumps({'changed_issuers': len(saved['updates']), 'provider_calls': 0,
                  'mode': 'verify' if args.verify else 'write',
                  'official_price_certification_added': False}))
