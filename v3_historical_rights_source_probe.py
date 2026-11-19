"""Check KRX subscription-rights coverage for the fixed historical holdouts."""
from collections import Counter
import json
import os
import re

from official_probe.clients import KRXClient, KRXProbeError, canonical_date


# Underlying code: (certificate ISIN, first listing date, listing notice).
LISTINGS = {
    "009830": ("KRA0098301G4", "2026-07-06", "https://kind.krx.co.kr/external/2026/07/01/000167/20260701000331/68915.htm"),
    "010170": ("KRA0101701G0", "2026-02-04", "https://kind.krx.co.kr/external/2026/01/30/000720/20260130002041/70926.htm"),
    "010640": ("KRA0106401G2", "2026-02-02", "https://kind.krx.co.kr/external/2026/01/28/000398/20260128001325/68915.htm"),
    "011790": ("KRA0117901G8", "2026-04-27", "https://spn.stockplus.com/news/api/v1/disclosure_views/koscom/457733"),
    "012200": ("KRA0122001G0", "2026-08-19", "https://spn.stockplus.com/news/api/v1/disclosure_views/koscom/497518"),
    "100250": ("KRA1002501G2", "2026-02-09", "https://kind.krx.co.kr/external/2026/02/04/000398/20260204001341/68915.htm"),
    "147760": ("KRA1477601G6", "2026-06-04", "https://kind.krx.co.kr/external/2026/05/29/001976/20260529003141/70926.htm"),
    "328130": ("KRA3281301G5", "2026-04-07", "https://kind.krx.co.kr/external/2026/04/02/001140/20260402002509/70926.htm"),
    "462350": ("KRA4623501G5", "2026-05-18", "https://spn.stockplus.com/news/api/v1/disclosure_views/koscom/465854"),
}
STOP_REASONS = {"http_unauthorized", "http_forbidden", "http_not_found", "http_rate_limited"}


def positive_integer(value):
    return (isinstance(value, str) and len(value) <= 20
            and re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", value) is not None
            and int(value.replace(",", "")) > 0)


def evaluate(rows, code, isin, day):
    short_code = "J" + isin[3:-1]
    selected = [r for r in rows if r.get("ISU_CD") in (isin, short_code)]
    if not selected:
        return "certificate_not_returned"
    if len(selected) != 1:
        return "duplicate_certificate"
    row = selected[0]
    if (row.get("BAS_DD") != canonical_date(day)
            or row.get("TARSTK_ISU_SRT_CD") not in (code, "A" + code)):
        return "certificate_identity_or_date_mismatch"
    if not all(positive_integer(row.get(field)) for field in ("TDD_CLSPRC", "ACC_TRDVOL")):
        return "no_verified_traded_close"
    return "verified_traded_close"


def probe(client):
    counts = Counter()
    for code, (isin, day, _) in LISTINGS.items():
        try:
            status = evaluate(client.subscription_rights(day), code, isin, day)
        except KRXProbeError as error:
            status = error.reason
        counts[status] += 1
        if status in STOP_REASONS:
            break
    return {
        "planned_certificates": len(LISTINGS),
        "requests_attempted": sum(counts.values()),
        "outcomes": dict(counts),
        "raw_prices_retained": False,
        "performance_validated": False,
    }


if __name__ == "__main__":
    key = os.getenv("KRX_API_KEY", "").strip()
    report = probe(KRXClient(key)) if key else {
        "planned_certificates": len(LISTINGS),
        "requests_attempted": 0,
        "outcomes": {"credential_not_configured": 1},
        "raw_prices_retained": False,
        "performance_validated": False,
    }
    print(json.dumps(report, sort_keys=True))
