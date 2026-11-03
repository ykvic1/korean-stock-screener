"""Execute the real browser quality renderer in Node without network or DOM."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node unavailable")
class QuantSourceQualityViewTests(unittest.TestCase):
    def test_same_day_complete_audit_only(self):
        script = r"""
const assert = require('node:assert/strict');
const ui = require('./docs/workspace.js');
const missing = Array.from({length:51}, (_,i)=>({
  code:String(i+1).padStart(6,'0'),
  reason_code:'history_file_missing'
}));
const report = {
  status:'exploratory', asof:'2026-11-02', missing,
  source_quality_audit:{
    requested_symbols:120,valid_ohlcv_symbols:69,excluded_symbols:51,
    excluded_reason_counts:{history_file_missing:51},
    all_exclusions_listed:true,official_adjusted_prices_certified:false,
    delisted_historical_universe_included:false
  }
};
const shown=ui.quantSourceQualityView(report,'2026-11-02');
assert.match(shown.summary,/69\/120/);
assert.match(shown.text,/이력 파일 부재 51종목/);
assert.equal(ui.quantSourceQualityView(report,'2026-11-03'),null);
assert.equal(ui.quantSourceQualityView({...report,source_quality_audit:{
  ...report.source_quality_audit,excluded_symbols:50
}},'2026-11-02'),null);
assert.equal(ui.quantSourceQualityView({...report,missing:missing.slice(0,50)},
  '2026-11-02'),null);
assert.equal(ui.quantSourceQualityView({...report,missing:[
  ...missing.slice(0,50),{...missing[50],reason_code:'ohlc_range_invalid'}
]},'2026-11-02'),null);
assert.equal(ui.quantSourceQualityView({...report,source_quality_audit:{
  ...report.source_quality_audit,official_adjusted_prices_certified:true
}},'2026-11-02'),null);
assert.equal(ui.quantSourceQualityView({status:'exploratory',asof:'2026-11-02'},
  '2026-11-02'),null);
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
