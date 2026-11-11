"""Stop-reference distance is information, never a stock-selection gate."""
import unittest

import quant_engine
import quant_engine_v22


class StopReferenceOnlyGates(unittest.TestCase):
    def test_swing_and_medium_checklist_do_not_depend_on_stop(self):
        sample = {
            "patterns": ["falling_wedge"],
            "flows_confirmed": True,
            "ma120w_gap": 2.0,
            "relative60": 4.0,
            "risk_flags": [],
        }
        for engine in (quant_engine, quant_engine_v22):
            with self.subTest(engine=engine.__name__):
                for stop in (-1, -6, -20, None):
                    with self.subTest(stop=stop):
                        result = engine.entry_playbooks({**sample, "stop_pct": stop})
                        self.assertTrue(result["스윙"]["testable_now"])
                        self.assertTrue(result["중기"]["testable_now"])
                chased = engine.entry_playbooks({
                    **sample, "stop_pct": -6, "risk_flags": ["단기 급등"]})
                self.assertFalse(chased["스윙"]["testable_now"])
                no_flow = engine.entry_playbooks({
                    **sample, "stop_pct": -6, "flows_confirmed": False})
                self.assertFalse(no_flow["중기"]["testable_now"])


if __name__ == "__main__":
    unittest.main()
