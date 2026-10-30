"""FCF missingness display must reflect source-only, matched-date public audits."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FcfGapPublicViewTests(unittest.TestCase):
    def test_both_mobile_public_entries_expose_one_expandable_source_audit(self):
        for name in ("index.html", "workspace.html"):
            with self.subTest(file=name):
                html = (ROOT / "docs" / name).read_text(encoding="utf8")
                self.assertEqual(html.count('id="fcfMissingness"'), 1)
                self.assertEqual(html.count('src="v3-fcf-gap.js"'), 1)
                self.assertIn("3개년 FCF 미확보 원인", html)

    def test_actual_public_gap_counts_partition_current_market(self):
        d = ROOT / "docs"
        current = json.loads((d / "v3-current-observed-all-listed-summary.json")
                             .read_text(encoding="utf8"))
        gap = json.loads((d / "v3-dart-cashflow-gap-summary.json")
                         .read_text(encoding="utf8"))
        if gap["asof"] != current["asof"]:
            # Safe day rollover: old cashflow snapshot is not a current
            # investment input and must never be displayed as up to date.
            self.assertLess(gap["asof"], current["asof"])
            self.assertLessEqual(gap["listed_issuer_count"], current["listed_total"])
        else:
            self.assertEqual(gap["listed_issuer_count"], current["listed_total"])
        depth = gap["three_year_issuer_depth"]
        self.assertEqual(sum(depth.values()), gap["listed_issuer_count"])
        self.assertEqual(sum(gap["categories"].values()),
                         gap["attempted_company_years"])
        self.assertEqual(gap["observed_fcf_company_years"] +
                         gap["missing_fcf_company_years"],
                         gap["attempted_company_years"])
        self.assertFalse(gap["trading_score_promoted"])
        self.assertTrue(gap["missing_ifrs_account_not_interpreted_as_zero"])

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_browser_render_is_real_only_and_mismatches_fail_closed(self):
        script = r"""
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const path='docs/';
const js=fs.readFileSync(path+'v3-fcf-gap.js','utf8');
const c=JSON.parse(fs.readFileSync(path+'v3-current-observed-all-listed-summary.json'));
const g=JSON.parse(fs.readFileSync(path+'v3-dart-cashflow-gap-summary.json'));
async function once(change) {
  const current=structuredClone(c), gap=structuredClone(g);
  if(change)change(gap,current);
  const el={textContent:'init'};
  const document={getElementById(id) {
    return id==='fcfMissingness'?el:
      id==='reload'?{addEventListener(){}}:null;
  }};
  const fetch=async url=>({
    ok:true,json:async()=>url.startsWith('v3-dart-cashflow')?gap:current
  });
  vm.runInNewContext(js,{document,fetch,Promise,Date,Number});
  await new Promise(resolve=>setImmediate(resolve));
  return el.textContent;
}
(async()=>{
  const live=await once(null);
  if(c.asof===g.asof&&c.listed_total===g.listed_issuer_count) {
    assert.match(live,/동일 연도·범위·접수번호/);
  } else {
    assert.match(live,/표시를 보류/);
    assert.match(live,/결측은 0원으로 대체하지 않습니다/);
  }
  // Test the successful display using the cashflow snapshot's own census,
  // without falsely presenting it as the latest market-day data.
  const aligned=(a,b)=>{b.asof=a.asof;b.listed_total=a.listed_issuer_count;};
  const ok=await once(aligned);
  assert.match(ok,/동일 연도·범위·접수번호/);
  assert.ok(ok.includes(g.three_year_issuer_depth.three_year_source_receipt_complete.toLocaleString('ko-KR')));
  assert.ok(ok.includes(g.three_year_issuer_depth.three_years_attempted_missing_accounts.toLocaleString('ko-KR')));
  assert.match(ok,/0원으로 처리하지 않으며/);
  assert.match(await once((a,b)=>{aligned(a,b);a.asof='2026-10-01'}),/표시를 보류/);
  assert.match(await once((a,b)=>{aligned(a,b);a.categories.missing_both_capex+=1}),/표시를 보류/);
  assert.match(await once((a,b)=>{aligned(a,b);a.three_year_issuer_depth.no_saved_cashflow_years+=1}),/표시를 보류/);
  console.log('source-only FCF audit display: pass');
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        r = subprocess.run(["node", "-e", script], cwd=ROOT,
                           text=True, capture_output=True, timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
