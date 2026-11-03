"""Keep quant source changes connected to bounded same-day refresh."""
import unittest
from pathlib import Path


class QuantRefreshTriggersTests(unittest.TestCase):
    def test_collector_fix_rebuilds_only_bounded_research(self):
        workflow = (Path(__file__).resolve().parents[1]
                    / ".github/workflows/v3-ranked-quant-refresh.yml"
                    ).read_text(encoding="utf-8")
        push_block = workflow.split("  schedule:", 1)[0]
        for path in (
            "quant_engine.py",
            "quant_fetch_history.py",
            ".github/workflows/v3-ranked-quant-refresh.yml",
        ):
            self.assertIn("      - " + path, push_block)
        self.assertIn("--max-stocks 120", workflow)
        self.assertIn("raw 9-year prices", workflow)
        self.assertIn("korea-stock-screener-publication", workflow)


if __name__ == "__main__":
    unittest.main()
