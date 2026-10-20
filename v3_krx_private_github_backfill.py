"""Bounded official KRX 10-year history -> explicitly authorized PRIVATE GitHub repo.

One file per complete two-market session; original vendor bytes NEVER enter the
public repository, its Actions artifacts, source-visible stdout or Pages.
Requires user-owned *private* target repository, PAT, and retention permission.
No fallback to public storage. Five sessions / ten official calls per job.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
from io import BytesIO
import base64
import gzip
import json
import os
import re
from urllib import request, error

from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_session_candidates import candidate_dates
from v3_krx_history_collection import _snapshot_market

SCHEMA = "v3-krx-private-git-history-0.1"
PAIR_SCHEMA = "v3-private-krx-two-market-raw-0.1"
START = "2016-10-17"
END = "2026-10-16"
MAX_DAYS = 5
MAX_REMOTE_BYTES = 1_000_000
REPO = re.compile(r"[a-zA-Z0-9_.-]{1,80}/[a-zA-Z0-9_.-]{1,100}\Z")


class PrivateHistoryError(ValueError):
    """Do not disclose URLs, tokens, response bodies, or market price data."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("utf8")


def _pair_path(day):
    if (type(day) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",day)
        or date.fromisoformat(day).isoformat()!=day):
        raise PrivateHistoryError("invalid_trade_day")
    return f"krx/{day[:4]}/{day}.json.gz"


def _pack_pair(day, raw_by_market):
    """Only validated, lossless, private raw official KRX market observations."""
    if set(raw_by_market)!= {"KOSPI","KOSDAQ"}:
        raise PrivateHistoryError("incomplete_market_pair")
    metadata={}
    for market in ("KOSPI","KOSDAQ"):
        raw=raw_by_market[market]
        if type(raw) is not bytes or not raw or len(raw)>20_000_000:
            raise PrivateHistoryError("invalid_raw_market_size")
        try:
            rows=json.loads(raw)["OutBlock_1"]
            inspected=_snapshot_market(rows,raw,market=market,asof=day)
        except Exception:
            raise PrivateHistoryError("invalid_market_source_for_private_archive") from None
        metadata[market]={
            "source_rows":inspected["provider_rows_observed"],
            "numeric_candidates":inspected["observed_numeric_candidates"],
            "quarantined":inspected["quarantined_unpriced_candidates"],
            "sha256":sha256(raw).hexdigest(),"raw_bytes":len(raw),
        }
    payload={"schema":PAIR_SCHEMA,"asof":day,
             "raw_source_base64":{
                 market:base64.b64encode(raw_by_market[market]).decode("ascii")
                 for market in ("KOSPI","KOSDAQ")}}
    packed=gzip.compress(_canonical(payload),compresslevel=9,mtime=0)
    if len(packed)>MAX_REMOTE_BYTES:
        raise PrivateHistoryError("oversized_private_history_day")
    return packed,metadata


def _unpack_pair(day,packed):
    if type(packed) is not bytes or not 0<len(packed)<=MAX_REMOTE_BYTES:
        raise PrivateHistoryError("oversized_private_history_day")
    try:
        with gzip.GzipFile(fileobj=BytesIO(packed)) as handle:
            uncompressed=handle.read(45_000_001)
        if len(uncompressed)>45_000_000:
            raise ValueError()
        envelope=json.loads(uncompressed)
        if envelope.get("schema")!=PAIR_SCHEMA or envelope.get("asof")!=day:
            raise ValueError()
        raws={m:base64.b64decode(envelope["raw_source_base64"][m],validate=True)
              for m in ("KOSPI","KOSDAQ")}
        checked,stats=_pack_pair(day,raws)
        if checked!=packed:
            raise ValueError()
        return stats
    except Exception:
        raise PrivateHistoryError("private_day_content_validation_failed") from None


class PrivateGitStorage:
    def __init__(self,repository,token,*,transport=None):
        if (type(repository) is not str or not REPO.fullmatch(repository)
            or repository.casefold()=="ykvic1/korean-stock-screener"
            or type(token) is not str or len(token)<20):
            raise PrivateHistoryError("private_repository_or_token_not_configured")
        self.repository=repository
        self.token=token
        self.transport=transport or self._http

    def _http(self,method,path,body=None):
        url=f"https://api.github.com/repos/{self.repository}/{path}"
        headers={"Authorization":"Bearer "+self.token,
                 "Accept":"application/vnd.github+json",
                 "X-GitHub-Api-Version":"2022-11-28",
                 "User-Agent":"v3-private-history-research"}
        data=_canonical(body) if body is not None else None
        if body is not None:headers["Content-Type"]="application/json"
        req=request.Request(url,headers=headers,data=data,method=method)
        try:
            with request.urlopen(req,timeout=30) as reply:
                answer=reply.read(2_000_001)
                if len(answer)>2_000_000:raise PrivateHistoryError("private_api_response_too_large")
                return json.loads(answer)
        except error.HTTPError as exc:
            if exc.code==404 and method=="GET" and path.startswith("contents/"):
                return None
            raise PrivateHistoryError("private_storage_api_failed") from None
        except (ValueError,TimeoutError,OSError):
            raise PrivateHistoryError("private_storage_api_failed") from None

    def verify_private(self):
        info=self.transport("GET","")
        if (type(info) is not dict or info.get("private") is not True
            or str(info.get("full_name","")).casefold()!=self.repository.casefold()):
            raise PrivateHistoryError("target_repo_not_confirmed_private")
        return True

    def get_content(self,path):
        resp=self.transport("GET","contents/"+path)
        if resp is None:return None,None
        if (type(resp) is not dict or resp.get("type")!="file"
            or resp.get("encoding")!="base64" or type(resp.get("sha")) is not str):
            raise PrivateHistoryError("untrusted_private_blob")
        try:return base64.b64decode(resp["content"],validate=False),resp["sha"]
        except Exception:raise PrivateHistoryError("invalid_private_blob_base64") from None

    def put_new(self,path,raw,comment):
        if type(raw) is not bytes or not 0<len(raw)<=MAX_REMOTE_BYTES:
            raise PrivateHistoryError("invalid_private_blob_length")
        if not (type(comment) is str and comment.isascii() and len(comment)<100):
            raise PrivateHistoryError("invalid_private_archive_commit")
        self.transport("PUT","contents/"+path,{
            "message":comment,"content":base64.b64encode(raw).decode("ascii")})
        return True

    def replace_manifest(self,raw,sha):
        if type(sha) is not str or not sha or len(raw)>MAX_REMOTE_BYTES:
            raise PrivateHistoryError("invalid_private_manifest_replacement")
        self.transport("PUT","contents/krx/manifest.json",{
            "message":"Advance private bounded KRX historical manifest",
            "content":base64.b64encode(raw).decode("ascii"),
            "sha":sha})


def _check_manifest(raw,days):
    if raw is None:
        return {"schema":SCHEMA,"start":START,"end":END,"completed":[]}
    try:
        payload=json.loads(raw)
        completed=payload["completed"]
        if (payload["schema"]!=SCHEMA or payload["start"]!=START
            or payload["end"]!=END or type(completed) is not list
            or len(completed)>len(days)
            or any(type(x) is not dict or x.get("day")!=days[i]
                   or type(x.get("pair_sha256")) is not str
                   or not re.fullmatch(r"[0-9a-f]{64}",x["pair_sha256"])
                   for i,x in enumerate(completed))):
            raise ValueError()
        return payload
    except Exception:
        raise PrivateHistoryError("invalid_private_manifest") from None


def run(*,storage,client,dates,limit=MAX_DAYS):
    """Only full KRX two-market pairs committed; manifest advances last.

    If a previous crash left a complete per-day file, inspect and resume without
    requesting KRX again. Never overwrite a conflicting immutable private pair.
    """
    if type(limit) is not int or not 1<=limit<=MAX_DAYS:
        raise PrivateHistoryError("invalid_daily_limit")
    if (type(dates) is not list or dates!=sorted(set(dates)) or
        not 5<=len(dates)<=6000):
        raise PrivateHistoryError("invalid_candidate_calendar")
    storage.verify_private()
    raw,manifest_sha=storage.get_content("krx/manifest.json")
    manifest=_check_manifest(raw,dates)
    completed=list(manifest["completed"])
    requested=0;resumed=0
    for day in dates[len(completed):len(completed)+limit]:
        path=_pair_path(day)
        prior,sha=storage.get_content(path)
        if prior is not None:
            _unpack_pair(day,prior)
            blob=prior
            resumed+=1
        else:
            raws={}
            for market in ("KOSPI","KOSDAQ"):
                try:
                    rows,returned=client.daily_market(market,day)
                    # _pack_pair parses returned raw source against its rows.
                    if json.loads(returned)["OutBlock_1"]!=rows:raise ValueError()
                    raws[market]=returned
                except Exception:
                    raise PrivateHistoryError("official_krx_market_request_or_shape_failed") from None
                requested+=1
            blob,stats=_pack_pair(day,raws)
            storage.put_new(path,blob,"Store private observed KRX two-market "+day)
        completed.append({"day":day,"pair_sha256":sha256(blob).hexdigest()})
        # Publish manifest only after the durable private day blob is present.
        next_manifest=_canonical({"schema":SCHEMA,"start":START,"end":END,
                                  "completed":completed})
        if manifest_sha is None:
            storage.put_new("krx/manifest.json",next_manifest,
                            "Create private KRX history manifest")
        else:
            storage.replace_manifest(next_manifest,manifest_sha)
        # Read back manifest SHA to avoid pretending another concurrent writer
        # did not commit a different head; isolated weekly job still locks.
        written,manifest_sha=storage.get_content("krx/manifest.json")
        if written!=next_manifest or not manifest_sha:
            raise PrivateHistoryError("private_manifest_not_durably_verified")
    return {
        "schema":"v3-krx-private-backfill-coverage-0.1",
        "status":"private_historical_source_stored",
        "total_candidate_days":len(dates),
        "durably_archived_pair_days":len(completed),
        "remaining_candidate_days":len(dates)-len(completed),
        "new_official_market_calls":requested,
        "orphaned_private_days_reconciled":resumed,
        "historical_adjusted_prices_certified":False,
        "official_trading_calendar_independently_verified":False,
        "market_raw_published":False,"ias_flow_scored":False,
    }


def main():
    import argparse
    parser=argparse.ArgumentParser(description="Private KRX history archive; blocked without private target")
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--start",default=START)
    parser.add_argument("--end",default=END)
    args=parser.parse_args()
    if not args.live:
        print(json.dumps({"status":"dry_run","provider_calls":0,
                          "private_history_archived":False}))
        return 0
    try:
        if (os.getenv("V3_KRX_PRIVATE_STORAGE_AUTHORIZED")!="true"
            or not os.getenv("KRX_API_KEY")
            or not os.getenv("V3_PRIVATE_ARCHIVE_REPO")
            or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN")):
            print(json.dumps({"status":"blocked",
                              "reason":"private_archive_storage_or_permission_not_configured",
                              "provider_calls":0}))
            return 0
        if args.start!=START or args.end!=END:
            raise PrivateHistoryError("historical_range_change_requires_new_manifest")
        weekdays=calendar_weekday_candidates(START,END)
        chosen=candidate_dates(weekdays,source="xkrx")["dates"]
        from official_probe.clients import KRXClient
        storage=PrivateGitStorage(os.environ["V3_PRIVATE_ARCHIVE_REPO"],
                                  os.environ["V3_PRIVATE_ARCHIVE_TOKEN"])
        result=run(storage=storage,client=KRXClient(os.environ["KRX_API_KEY"]),
                   dates=chosen)
        print(json.dumps(result,sort_keys=True))
        return 0
    except Exception:
        # Never allow third-party HTTP exception messages in PUBLIC Actions logs.
        print(json.dumps({"status":"blocked",
                          "reason":"private_krx_source_or_archive_validation_failed",
                          "safely_redacted":True}))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
