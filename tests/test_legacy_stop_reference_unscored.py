"""The legacy screening export must not reward or penalize stop distance."""

import ast
from copy import deepcopy
from pathlib import Path
import unittest


def legacy_score_function():
    """Exercise the real scoring function without importing network providers."""
    source = Path(__file__).resolve().parents[1] / 'screener.py'
    tree = ast.parse(source.read_text(encoding='utf8'))
    definitions = [node for node in tree.body
                   if (isinstance(node, ast.FunctionDef) and node.name == 'score_one')
                   or (isinstance(node, ast.Assign) and any(
                       isinstance(target, ast.Name) and target.id == 'WEIGHTS'
                       for target in node.targets))]
    scope = {'round_or_none': lambda x, n=1: round(x, n) if x is not None else None}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), 'exec'), scope)
    return scope['score_one'], scope['WEIGHTS']


class LegacyStopReferenceOnlyTests(unittest.TestCase):
    def _row(self, stop_pct, stop_line, reward_risk):
        return {
            'price20': 4, 'price60': 12, 'volume_ratio': 1.4,
            'patterns': [], 'one_day_volume_ratio': 1.3,
            'obv_divergence': True, 'obv_rising': True,
            'is_breakout': False, 'bullish_candle': False,
            'compression': True, 'sma_aligned': False,
            'ma20_above_ma60': True, 'is_wedge': False,
            'is_ihs': False, 'is_triangle': False,
            'close_vs_ma60_pct': 2, 'weekly_aligned': False,
            'weekly_turn': False, 'ma120w_gap': 1,
            'flows_confirmed': False, 'relative60': 2, 'relative20': 1,
            'pbr': 1.1, 'high52_gap': -12, 'trading_eok': 15,
            'stop_pct': stop_pct, 'stop_line': stop_line,
            'reward_risk': reward_risk,
        }

    def test_stop_only_fields_never_change_score_availability_or_decision(self):
        score_one, weights = legacy_score_function()
        cfg = {'preferred_pbr_max': 1.5}
        results = [score_one(deepcopy(self._row(*v)), cfg)
                   for v in ((-1, 99, 0.2), (-8, 92, 2.3),
                             (-35, 65, 12), (None, None, None))]
        observed = [tuple(result[key] for key in
                          ('components', 'raw_score', 'available_max',
                           'score', 'coverage_pct', 'decision', 'risk_flags'))
                    for result in results]
        self.assertTrue(all(item == observed[0] for item in observed))
        self.assertNotIn('위험·손익비', weights)
        self.assertEqual(sum(weights.values()), 95)
        self.assertNotIn('위험·손익비', results[0]['components'])


if __name__ == '__main__':
    unittest.main()
