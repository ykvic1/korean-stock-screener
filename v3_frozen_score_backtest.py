"""Bridge FROZEN historical six-pillar scores to event/PIT-gated walkforward.

Research-only: no score fitting, trades, hit-probability forecast, or release.
A historical signal must carry a score snapshot asserted BEFORE/AT signal,
never computed from realized future candles. Assertions are not authentication.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo
import math

from v3_walkforward_quality_gate import gated_walkforward, ResearchGateError
from v3_walkforward_research import HORIZONS, _outcome, WalkForwardError
from v3_factor_overlap_audit import _spearman

KST=ZoneInfo("Asia/Seoul")
MAX_CASES=120

class ScoredBacktestError(ValueError):
    """Fixed privacy-safe errors."""


def _date(s):
    try:
        if type(s) is not str or date.fromisoformat(s).isoformat()!=s:
            raise ValueError()
        return date.fromisoformat(s)
    except ValueError:
        raise ScoredBacktestError("invalid_backtest_date") from None


def _instant(s):
    try:
        if type(s) is not str or not s.endswith("Z"):
            raise ValueError()
        d=datetime.strptime(s,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return d
    except ValueError:
        raise ScoredBacktestError("invalid_frozen_score_timestamp") from None


def scored_walkforward(payloads, *, split_date, embargo_days=252,
                       round_trip_cost_bps=30):
    cutoff=_date(split_date)
    if not isinstance(payloads,list) or len(payloads)>MAX_CASES:
        raise ScoredBacktestError("invalid_historical_scored_cohort")
    if type(embargo_days) is not int or not 0<=embargo_days<=730:
        raise ScoredBacktestError("invalid_embargo")
    if type(round_trip_cost_bps) not in (int,float) or not 0<=round_trip_cost_bps<=500:
        raise ScoredBacktestError("invalid_fees")
    seen=set()
    frozen=[]
    for p in payloads:
        if not isinstance(p,dict) or not isinstance(p.get("case"),dict):
            raise ScoredBacktestError("invalid_historical_payload")
        case=p["case"]
        signal=_date(case.get("signal_date"))
        score=p.get("frozen_score_snapshot")
        if not isinstance(score,dict) or any(
                score.get(k)!=case.get(k) for k in ("code","issuer_id","market")):
            raise ScoredBacktestError("score_issuer_mismatch")
        if score.get("asof")!=signal.isoformat():
            raise ScoredBacktestError("hindsight_score_asof")
        when=_instant(score.get("frozen_at_utc"))
        signal_decision=_instant(case.get("decision_at_utc"))
        if (when>signal_decision or when.astimezone(KST).date()!=signal
                or when.astimezone(KST).hour<15 or
                (when.astimezone(KST).hour==15 and when.astimezone(KST).minute<30)):
            raise ScoredBacktestError("score_not_frozen_after_close_before_decision")
        digest=case.get("evidence",{}).get("signal_snapshot_sha256")
        if (type(digest) is not str or len(digest)!=64
                or score.get("snapshot_sha256")!=digest):
            raise ScoredBacktestError("score_and_signal_digest_mismatch")
        value=score.get("candidate_full_total_research_only")
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=100:
            raise ScoredBacktestError("unusable_frozen_six_pillar_research_score")
        if (score.get("confirmed_score") is not None
                or score.get("confirmed_buy") is not False):
            raise ScoredBacktestError("fabricated_confirmed_score_claim")
        identity=(case.get("issuer_id"),case.get("code"),signal)
        if identity in seen:
            raise ScoredBacktestError("duplicate_frozen_issuer_signal")
        seen.add(identity)
        frozen.append((p,signal,float(value)))
    # This is the authoritative existing PIT/corporate-event walkforward
    # research engine. It still does NOT certify real historical sources.
    try:
        aggregate=gated_walkforward(
            [{k:v for k,v in p.items() if k!="frozen_score_snapshot"}
             for p,_,_ in frozen],
            split_date=split_date,embargo_days=embargo_days,
            round_trip_cost_bps=round_trip_cost_bps)
    except (ResearchGateError,WalkForwardError,TypeError,KeyError):
        raise ScoredBacktestError("underlying_quality_gate_rejected_case") from None
    analysis={}
    for market in ("KOSPI","KOSDAQ"):
        analysis[market]={}
        for label,days in HORIZONS.items():
            groups={"train":[],"holdout":[]}
            exclusions={"quality_censored":0,"split_purged":0,"short_history":0,
                        "overlapping_same_issuer":0}
            last_end={"train":{},"holdout":{}}
            for p,signal,value in sorted(frozen,key=lambda x:(x[1],x[0]["case"]["code"])):
                case=p["case"]
                if case.get("market")!=market:
                    continue
                # Reuse the same quality censor contract PER CASE to exclude
                # retrospective future event labels without pretending to
                # predict those events at signal date.
                try:
                    result=gated_walkforward(
                        [{k:v for k,v in p.items() if k!="frozen_score_snapshot"}],
                        split_date=split_date,embargo_days=embargo_days,
                        round_trip_cost_bps=round_trip_cost_bps)
                except (ResearchGateError,WalkForwardError,TypeError,KeyError):
                    raise ScoredBacktestError("underlying_quality_gate_rejected_case") from None
                cell=result["periods"][label]
                if cell["quality_exclusion_counts"].get("right_censored_short_history"):
                    exclusions["short_history"]+=1
                    continue
                if cell["quality_exclusion_counts"] or not cell["internally_eligible_cases_submitted"]:
                    exclusions["quality_censored"]+=1
                    continue
                bars=case["bars"]
                if len(bars)<days:
                    exclusions["short_history"]+=1
                    continue
                if signal<cutoff and _date(bars[days-1]["date"])<cutoff:
                    group="train"
                elif signal>=cutoff+timedelta(days=embargo_days):
                    group="holdout"
                else:
                    exclusions["split_purged"]+=1
                    continue
                prev=last_end[group].get(case["issuer_id"])
                if prev and signal<=prev:
                    exclusions["overlapping_same_issuer"]+=1
                    continue
                last_end[group][case["issuer_id"]]=_date(bars[days-1]["date"])
                outcome=_outcome(case,days,round_trip_cost_bps)
                if outcome is None:
                    exclusions["short_history"]+=1
                    continue
                groups[group].append((value,outcome["net_return_pct"],
                    case["issuer_id"],signal))
            per={}
            for group,items in groups.items():
                issues=len(set(x[2] for x in items))
                quarters=len(set((x[3].year,(x[3].month-1)//3) for x in items))
                minimum=len(items)>=40 and issues>=10 and quarters>=12
                correlation=_spearman([(x[0],x[1]) for x in items]) if minimum else None
                per[group]={
                    "eligible_score_label_pairs":len(items),
                    "distinct_issuers":issues,"distinct_signal_quarters":quarters,
                    "exploratory_sample_floor_met":minimum,
                    "score_to_realized_net_return_spearman_research_only":
                        round(correlation,6) if correlation is not None else None,
                    "actual_future_prediction_accuracy":None,
                }
            analysis[market][label]={"cohorts":per,"exclusions":exclusions}
    return {
        "schema":"v3-frozen-score-quality-walkforward-0.1",
        "input_case_count":len(frozen),"split_date":split_date,
        "horizons_trading_session_proxies":dict(HORIZONS),
        "research_quality_gated_walkforward":aggregate,
        "frozen_score_vs_outcomes_research_only":analysis,
        "snapshot_digests_are_untrusted_assertions":True,
        "historical_issuer_roster_independently_authenticated":False,
        "past_score_weights_predeclared_verified":False,
        "censoring_bias_resolved":False,"model_validated_out_of_sample":False,
        "forecast":None,"confirmed_score":None,"confirmed_buy":False,
        "v3_release_ready":False,
    }
