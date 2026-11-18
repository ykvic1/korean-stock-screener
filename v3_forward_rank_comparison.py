"""Compare disjoint frozen ranks at a common KRX market composition."""
from fractions import Fraction
from math import ceil, floor
from statistics import fmean


def sample(codes, openings, returns):
    opened = sum(openings[code][0] == 'official_open_observed' for code in codes)
    observed = [returns[code] for code in codes if code in returns]
    mean = fmean(observed) if observed else None
    count = len(observed)
    losses = sum(value < 0 for value in observed)
    return {
        'selected_count': len(codes), 'confirmed_opening_count': opened,
        'valued_count': count, 'negative_return_count': losses,
    }, mean


def weighted_comparison(members, openings, returns, top, last):
    candidates, references = {}, {}
    for code, row in members.items():
        market, rank = row[1], row[3]
        if rank is None or (last is not None and rank > last):
            continue
        group = candidates if rank <= top else references
        group.setdefault(market, []).append(code)

    total = sum(map(len, candidates.values()))
    markets = {}
    return_gaps, loss_gaps = [], []
    for market, codes in sorted(candidates.items()):
        chosen, chosen_mean = sample(codes, openings, returns)
        peers, peer_mean = sample(references.get(market, ()), openings, returns)
        weight = Fraction(len(codes), total)
        markets[market] = {'candidate': chosen, 'reference': peers}
        if chosen_mean is not None and peer_mean is not None:
            return_gaps.append(weight * (chosen_mean - peer_mean))
        a, b = chosen['confirmed_opening_count'], peers['confirmed_opening_count']
        if a and b:
            a_loss, b_loss = chosen['negative_return_count'], peers['negative_return_count']
            a_gap, b_gap = a - chosen['valued_count'], b - peers['valued_count']
            loss_gaps.append((
                weight * (Fraction(a_loss, a) - Fraction(b_loss + b_gap, b)),
                weight * (Fraction(a_loss + a_gap, a) - Fraction(b_loss, b)),
            ))
    loss_bounds = None
    if markets and len(loss_gaps) == len(markets):
        lower = sum(term[0] for term in loss_gaps)
        upper = sum(term[1] for term in loss_gaps)
        loss_bounds = [floor(lower * 1_000_000) / 10_000, ceil(upper * 1_000_000) / 10_000]
    return {
        'reference_min_rank': top + 1, 'reference_max_rank': last,
        'weight_basis': 'frozen_candidate_market_mix',
        'markets': markets,
        'observed_mean_return_spread_pct_points': round(sum(return_gaps), 4)
        if markets and len(return_gaps) == len(markets) else None,
        'loss_rate_spread_bounds_pct_points': loss_bounds,
    }


def add_rank_comparisons(groups, members, openings, returns):
    for label, top, last in (('top20', 20, 100), ('top100', 100, None)):
        groups[label]['same_market_rank_comparison'] = weighted_comparison(
            members, openings, returns, top, last)
