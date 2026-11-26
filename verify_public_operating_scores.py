"""Verify the exact operating snapshot served on the public screener."""
import argparse
import csv
from datetime import date
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from urllib.request import Request, urlopen

from v3_operational_research_score import OUTPUT, to_csv

CSV = "v3-operational-research-scores.csv"
PAGES = "https://ykvic1.github.io/korean-stock-screener/"


def download(filename):
    request = Request(PAGES + filename, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=25) as response:
        return response.read()


def verify(score_json, score_csv, expected_asof):
    report = json.loads(score_json)
    if report["asof"] != expected_asof:
        raise ValueError(f"Expected {expected_asof}; JSON is {report['asof']}")
    rows = report["rows"]
    if not rows or len(rows) != report["listed_total"]:
        raise ValueError("JSON roster count differs from listed_total")
    expected_csv = to_csv(report)
    actual_csv = score_csv.decode("utf-8-sig")
    if actual_csv != expected_csv:
        # Compare parsed cells, so equivalent newline or quoting conventions pass.
        actual_rows = list(csv.reader(StringIO(actual_csv, newline="")))
        expected_rows = list(csv.reader(StringIO(expected_csv, newline="")))
        if len(actual_rows) != len(expected_rows):
            raise ValueError(
                f"CSV row count {len(actual_rows) - 1}; expected {len(rows)}"
            )
        for index, (actual, expected) in enumerate(zip(actual_rows, expected_rows), 1):
            if actual != expected:
                raise ValueError(f"CSV and JSON differ at CSV line {index}")
    return {
        "asof": report["asof"],
        "listed_total": len(rows),
        "ranked_total": sum(row["rank"] is not None for row in rows.values()),
        "prior_date_rows": sum(row["asof"] < expected_asof for row in rows.values()),
        "json_sha256": sha256(score_json).hexdigest(),
        "csv_sha256": sha256(score_csv).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asof", required=True, type=date.fromisoformat)
    parser.add_argument("--docs", type=Path, help="Check a local snapshot instead of live Pages")
    args = parser.parse_args()
    if args.docs:
        score_json = (args.docs / OUTPUT).read_bytes()
        score_csv = (args.docs / CSV).read_bytes()
    else:
        score_json = download(OUTPUT)
        score_csv = download(CSV)
    result = verify(score_json, score_csv, args.asof.isoformat())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
