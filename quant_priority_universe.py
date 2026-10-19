"""Select actual current-day scored research targets BEFORE quant peer universe.

Deep-analysis candidates are prioritized but not promoted to BUY, and
the remaining universe is filled with existing v2 ranked signals.
The same selection is used for FDR collection AND quant calculation,
avoiding historical absence of recommendations from the fixed top-120 slice.
No delisted raw archive, no future data, no synthetic references.
"""
from __future__ import annotations
from pathlib import Path
import json
import re

def prioritized_stocks(data:dict, recommendations:dict|None, limit:int=120)->list[dict]:
    if not isinstance(data,dict) or data.get("status")!="success" or (
            type(data.get("date")) is not str) or not isinstance(data.get("stocks"),list):
        raise ValueError("invalid_current_market_data")
    if type(limit) is not int or not 20<=limit<=500:
        raise ValueError("invalid_target_universe_size")
    rows={};ordered=[]
    for r in data["stocks"]:
        code=r.get("code") if isinstance(r,dict) else None
        if (isinstance(code,str) and re.fullmatch(r"\d{6}",code) and
                code not in rows and r.get("price_date")==data["date"] and
                isinstance(r.get("price"),(int,float)) and r["price"]>0):
            rows[code]=r
            ordered.append(code)
    if len(rows)<20:raise ValueError("too_few_current_price_rows")
    priority=[]
    if (isinstance(recommendations,dict) and
            recommendations.get("asof")==data["date"] and
            recommendations.get("research_source_matched") is True and
            isinstance(recommendations.get("dossiers"),list)):
        for d in recommendations["dossiers"]:
            if not isinstance(d,dict):continue
            code=d.get("code")
            if code in rows and code not in priority and (
                    d.get("qualification")=="needs_deeper_evidence"):
                priority.append(code)
    codes=(priority+[c for c in ordered if c not in priority])[:limit]
    return [rows[c] for c in codes]

def optional_recommendations(path:Path|str|None)->dict|None:
    if path is None:return None
    f=Path(path)
    if not f.exists() or f.stat().st_size>4_000_000:return None
    try:return json.loads(f.read_text(encoding="utf-8"))
    except (OSError,ValueError,UnicodeError):return None
