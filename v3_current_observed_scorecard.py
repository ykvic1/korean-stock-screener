"""Same-close all-listed provisional scorecard, never blocked by older DART.

Only independently replayed saved market evidence:
volume/OBV <=16/20, technical <=25/25, momentum <=8/10.
Financial15, valuation15, industry15 remain EMPTY until same-day, verified
official sources have actually supplied them. No rescaling or score certification.
"""
from __future__ import annotations
import argparse
import csv
from io import StringIO
import json
from pathlib import Path

from v3_volume_participation import build as volume_build
from v3_current_price_replay import build as price_build
from v3_all_listed_scorecard import _momentum, _contribution
from v3_weekly_rotation import technical_points
from v3_dart_bulk_history import _stable_digest

SCHEMA="v3-current-day-all-listed-three-source-provisional-0.1"
POINT_BUDGET={"volume":20,"technical":25,"financial":15,
              "valuation":15,"industry":15,"momentum":10}

class LivePartialScorecardError(ValueError):
    """Never include raw provider data in failures."""


def generate(market,catalog,volume,price):
    """Compute all current listed tickers; verify EVERY reused source row."""
    true_volume=volume_build(market,catalog)
    true_price=price_build(market,catalog)
    if volume!=true_volume or price!=true_price:
        raise LivePartialScorecardError("not_exact_same_day_source_replay")
    asof=catalog["date"]
    if asof!=market["date"] or volume["asof"]!=asof or price["asof"]!=asof:
        raise LivePartialScorecardError("different_market_date")
    observed={r["code"]:r for r in market["stocks"]}
    output=[]
    counts={"volume":0,"technical":0,"momentum":0}
    nonblank=0
    for listed in sorted(catalog["rows"],key=lambda r:r["code"]):
        code=listed["code"]
        v=volume["records"].get(code)
        p=price["rows"].get(code)
        values={"volume":None,"technical":None,"financial":None,
                "valuation":None,"industry":None,"momentum":None}
        coverage={"volume":0,"technical":0,"financial":0,
                  "valuation":0,"industry":0,"momentum":0}
        if v is not None:
            values["volume"],coverage["volume"]=_contribution(
                v["observed_points"],v["observed_available_max"],20)
        if p is not None:
            if (p["market"]!=listed["market"] or p["asof"]!=asof or
                p["official_investment_score_certified"] is not False):
                raise LivePartialScorecardError("invalid_market_price_replay_identity")
            t=p["technical_research_reference"]
            if t is not None:
                if code not in observed:
                    raise LivePartialScorecardError("unpriced_technical_candidate")
                points,available,_=technical_points(observed[code],asof)
                if (not available or points is None or
                    t.get("technical_observed_maximum")!=available):
                    raise LivePartialScorecardError("inconsistent_replayed_technical")
                values["technical"],coverage["technical"]=_contribution(
                    points,available,25)
            mom=p["momentum_research_reference"]
            if mom is not None:
                wrapper={"momentum_research_reference":mom,
                         "momentum_available_weight":mom["momentum_observed_maximum"],
                         "momentum_research_points":mom["momentum_research_candidate"]}
                values["momentum"],coverage["momentum"]=_momentum(
                    wrapper,asof,listed["market"])
                if coverage["momentum"]==0:
                    raise LivePartialScorecardError("invalid_replayed_momentum")
        for area in counts:
            if coverage[area]>0:
                counts[area]+=1
        available=sum(coverage.values())
        earned=sum(v for v in values.values() if v is not None)
        if not 0<=earned<=available<=49:
            raise LivePartialScorecardError("invented_current_day_score")
        if available:
            nonblank+=1
        output.append({
            "code":code,"name":listed["name"],"market":listed["market"],
            "asof":asof,"earned":round(earned,4) if available else None,
            "available":available,"unavailable":100-available,
            "factors":values,"factor_available":coverage,
            "official_score":None,"certified":False,
        })
    if (len(output)!=catalog["listed_total"]
        or counts["volume"]!=volume["observed_volume_candidate_issuers"]
        or counts["technical"]!=price["technical_research_issuer_count"]
        or counts["momentum"]!=price["momentum_research_issuer_count"]):
        raise LivePartialScorecardError("source_counts_do_not_reconcile")
    summary={
        "schema":SCHEMA,"asof":asof,"listed_total":len(output),
        "observed_any_factor_issuers":nonblank,
        "observed_issuer_counts":{**counts,"financial":0,"valuation":0,"industry":0},
        "area_budgets":dict(POINT_BUDGET),
        "highest_currently_observable_weight":49,
        "official_investor_flows_inferred":False,
        "same_day_financial_and_sector_qualification":False,
        "missing_weight_is_not_zero":True,
        "not_an_investment_ranking":True,
        "certified_100point_count":0,
        "provider_requests":0,
        "source_sha256":{
            "market":_stable_digest(market),"catalog":_stable_digest(catalog),
            "volume":_stable_digest(volume),"price":_stable_digest(price),
        },
    }
    return output,summary


def to_csv(rows,summary):
    sink=StringIO(newline="")
    out=csv.writer(sink,lineterminator="\n")
    out.writerow(["종목코드","종목명","시장","가격자료기준일",
        "거래량_OBV_잠정점수","거래량_OBV_산출배점",
        "기술_잠정점수","기술_산출배점",
        "재무_잠정점수","밸류에이션_잠정점수","산업_잠정점수",
        "모멘텀_잠정점수","모멘텀_산출배점",
        "관측잠정합계_100점중","산출가능배점_100점중",
        "미확보배점_100점중","정식투자점수","투자등급"])
    for row in rows:
        v=row["factors"]
        a=row["factor_available"]
        out.writerow([
            row["code"],row["name"],row["market"],row["asof"],
            "" if v["volume"] is None else v["volume"],a["volume"] or "",
            "" if v["technical"] is None else v["technical"],a["technical"] or "",
            "","","",
            "" if v["momentum"] is None else v["momentum"],a["momentum"] or "",
            "" if row["earned"] is None else row["earned"],
            row["available"] or "",row["unavailable"],"","미검증 연구용"
        ])
    return "\ufeff"+sink.getvalue()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs",default="docs")
    parser.add_argument("--mode",choices=("write","verify"),default="write")
    args=parser.parse_args(argv)
    root=Path(args.docs)
    def read(name):
        return json.loads((root/name).read_text(encoding="utf8"))
    rows,summary=generate(
        read("data.json"),read("v22-market-catalog.json"),
        read("v3-volume-obv-observed.json"),
        read("v3-current-price-technical-momentum.json"))
    new_csv=to_csv(rows,summary)
    target=root/"v3-current-observed-all-listed.csv"
    status=root/"v3-current-observed-all-listed-summary.json"
    if args.mode=="write":
        target.write_text(new_csv,encoding="utf8")
        from v3_dart_bulk_history import write_json
        write_json(status,summary)
    else:
        if (not target.is_file() or not status.is_file()
            or target.read_text(encoding="utf8")!=new_csv
            or json.loads(status.read_text(encoding="utf8"))!=summary):
            raise LivePartialScorecardError("live_partial_publication_differs_from_sources")
    print(json.dumps({
        "status":"current_market_actual_observed_only",
        "asof":summary["asof"],
        "listed":summary["listed_total"],
        "observed":summary["observed_any_factor_issuers"],
        "by_area":summary["observed_issuer_counts"],
        "available_points_ceiling":49,"certified_100point":0,
    },sort_keys=True))


if __name__=="__main__":
    main()
