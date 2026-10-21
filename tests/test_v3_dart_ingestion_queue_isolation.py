"""No-network regression: DART collectors stay out of market/scorecard queue.

Each source collection is a unique bounded provider, with its own output files.
A failed Git push may retry the verified publication, NEVER the HTTP collection.
"""
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PATHS={
 "cash":".github/workflows/v3-dart-cashflow.yml",
 "industry":".github/workflows/v3-dart-industry-codes.yml",
 "fouryear":".github/workflows/v3-dart-bulk-four-year.yml",
}
GROUPS={
 "cash":"korea-stock-screener-dart-cashflow",
 "industry":"korea-stock-screener-dart-company-profiles",
 "fouryear":"korea-stock-screener-dart-fouryear-major",
}
ALLOWED={
 "cash":("docs/v3-dart-cashflow-reference.json",
         "docs/v3-dart-fcf-research-preview.json"),
 "industry":("docs/v3-dart-industry-classification.json",
             "docs/v3-dart-industry-earnings-sample.json"),
 "fouryear":("docs/v3-dart-bulk-history.json",
             "docs/v3-dart-bulk-provisional.json",
             "docs/v3-real-source-six-area-coverage.json",
             "docs/v3-real-source-six-area-progress.json"),
}
GUARDS={
 "cash":("python v3_dart_cashflow_enrichment.py --mode rebind",
         "python v3_dart_cashflow_factor_preview.py --mode write",
         "python v3_dart_cashflow_enrichment.py --mode verify",
         "python v3_dart_cashflow_factor_preview.py --mode verify"),
 "industry":("python v3_dart_industry_codes.py --mode rebind",
             "python v3_dart_industry_earnings_sample.py --mode write",
             "python v3_dart_industry_codes.py --mode verify",
             "python v3_dart_industry_earnings_sample.py --mode verify"),
 "fouryear":("python v3_dart_bulk_history.py --mode verify",
             "python v3_real_six_pillar_coverage_audit.py --mode verify",
             "python v3_real_source_progress_ledger.py --mode verify"),
}

class IsolatedDartWritersTests(unittest.TestCase):
    def test_each_dart_writer_owns_a_distinct_queue(self):
        seen=set()
        for name,path in PATHS.items():
            source=(ROOT/path).read_text(encoding="utf8")
            group=GROUPS[name]
            self.assertIn("group: "+group,source,name)
            self.assertNotIn("group: korea-stock-screener-publication",source,name)
            self.assertNotIn("group: korea-stock-screener-scorecard-publisher",source,name)
            self.assertNotIn(group,seen)
            seen.add(group)
            self.assertIn("cancel-in-progress: false",source,name)

    def test_bounded_source_verified_nonforce_retry_without_requery(self):
        for name,path in PATHS.items():
            text=(ROOT/path).read_text(encoding="utf8")
            self.assertEqual(text.count("for attempt in 1 2 3; do"),1,name)
            before,loop=text.split("for attempt in 1 2 3; do",1)
            self.assertIn("git pull --rebase origin main",loop,name)
            self.assertIn("git push origin HEAD:main",loop,name)
            self.assertIn("git push origin HEAD:main; then",loop,name)
            self.assertIn("exit 1",loop,name)
            self.assertNotIn("git push --force",text,name)
            for guard in GUARDS[name]:
                self.assertIn(guard,loop,name)
            self.assertLess(loop.index("git pull --rebase origin main"),
                            loop.index("git push origin HEAD:main"),name)
            # All official API collection happens OUTSIDE the retry loop.
            self.assertNotIn("--mode live",loop,name)
            self.assertNotIn("requests.Session()",loop,name)
            self.assertNotIn("DART_API_KEY:",loop,name)
            self.assertIn("DART_API_KEY:",before,name)

    def test_collectors_commit_only_source_owned_documents(self):
        for name,path in PATHS.items():
            text=(ROOT/path).read_text(encoding="utf8")
            commands=[line.strip() for line in text.splitlines()
                      if line.strip().startswith("git add docs/")]
            self.assertGreater(len(commands),0,name)
            for command in commands:
                for part in command.split()[2:]:
                    self.assertIn(part,ALLOWED[name],(name,part))
            self.assertNotIn("git add docs/data.json",text,name)
            self.assertNotIn("git add docs/v22-market-catalog.json",text,name)
            self.assertNotIn("git add docs/v3-all-listed-scores.csv",text,name)
            self.assertNotIn("git add docs/v3-all-listed-scorecard-summary.json",text,name)

if __name__=="__main__":
    unittest.main()
