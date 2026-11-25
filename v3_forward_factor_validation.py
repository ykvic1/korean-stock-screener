"""V3.1 descriptive factor validation against the exact frozen operating score."""
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import subprocess

from v3_operational_research_score import SPECS, VERSION
from v3_score_observation_capture import SCORE_PATH
from v3_forward_rank_comparison import sample

MARKETS = ('KOSPI', 'KOSDAQ')
MIN_VALUED = 20


def frozen_score(root, observation):
    """Recover only the original score bytes; never recalculate with future data."""
    digest = observation.get('source_score_sha256')
    commit = observation.get('source_commit')
    if (observation.get('model_version') != VERSION
            or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
            or not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit)):
        return None
    raw = None
    try:
        candidate = (root / SCORE_PATH).read_bytes()
        if sha256(candidate).hexdigest() == digest:
            raw = candidate
    except OSError:
        pass
    if raw is None:
        command = ['git', 'show', f'{commit}:{SCORE_PATH}']
        try:
            raw = subprocess.check_output(command, cwd=root, stderr=subprocess.DEVNULL)
        except (subprocess.CalledProcessError, OSError):
            try:
                subprocess.run(['git', 'fetch', '--no-tags', '--depth=1', 'origin', commit],
                               cwd=root, check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60)
                raw = subprocess.check_output(command, cwd=root, stderr=subprocess.DEVNULL)
            except (subprocess.SubprocessError, OSError):
                return None
    if sha256(raw).hexdigest() != digest:
        return None
    try:
        report = json.loads(raw)
        members = observation['rows']
        rows = report['rows']
        if (report['version'] != VERSION or report['asof'] != observation['score_asof']
                or set(rows) != set(members)
                or any([rows[c][k] for k in ('name','market','score','rank','available','asof','oldest_asof')]
                       != m[:7] for c,m in members.items())):
            return None
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    return report


def factor_value(value, maximum, day):
    if not isinstance(value, list) or len(value) != 3:
        return 'missing'
    points, weight, asof = value
    if (type(points) not in (int,float) or not math.isfinite(points)
            or type(weight) not in (int,float) or not math.isfinite(weight)
            or not isinstance(asof,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',asof)
            or asof > day or not 0 < weight <= maximum + .000001
            or not 0 <= points <= weight + .000001):
        return 'missing'
    if abs(weight-maximum) > .000001:
        return 'partial'
    return 'high' if points >= maximum/2 else 'low'


def summarize(observation, openings, returns, report):
    """Fixed descriptive groups; no parameter fitting or rank changes."""
    if report is None:
        return {'status':'frozen_factor_source_unavailable'}
    day = observation['score_asof']
    ranked = {c:m for c,m in observation['rows'].items() if m[3] is not None}
    factors = {}
    for area,spec in SPECS.items():
        for key,maximum in spec.items():
            markets = {}
            for market in MARKETS:
                groups = {k:[] for k in ('high','low','partial','missing')}
                for code,member in ranked.items():
                    if member[1] != market:
                        continue
                    value = report['rows'][code].get('parts',{}).get(area,{}).get(key)
                    groups[factor_value(value,maximum,day)].append(code)
                high,high_mean = sample(groups['high'],openings,returns)
                low,low_mean = sample(groups['low'],openings,returns)
                high['observed_mean_return_pct'] = round(high_mean,4) if high_mean is not None else None
                low['observed_mean_return_pct'] = round(low_mean,4) if low_mean is not None else None
                markets[market] = {
                    'high':high, 'low':low,
                    'partial_weight_count':len(groups['partial']),
                    'missing_weight_count':len(groups['missing']),
                    'observed_mean_return_spread_pct_points':round(high_mean-low_mean,4)
                    if min(high['valued_count'],low['valued_count'])>=MIN_VALUED else None,
                }
            factors[f'{area}.{key}'] = {'maximum':maximum,'markets':markets}
    return {'status':'observed_descriptive_only','source_score_sha256':observation['source_score_sha256'],
            'threshold':'half_of_full_factor_weight','minimum_valued_per_side':MIN_VALUED,
            'factors':factors}
