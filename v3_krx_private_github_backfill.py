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
import time
from urllib import request, error

from v3_krx_private_backfill_plan import calendar_weekday_candidates
from v3_krx_session_candidates import candidate_dates
from v3_krx_history_collection import _snapshot_market

SCHEMA = "v3-krx-private-git-history-compact-0.2"
PAIR_SCHEMA = "v3-private-krx-two-market-binary-frame-0.2"
MAGIC = b"KRX2\0"
MAX_REPO_SIZE_KIB = 750*1024    # stop before ~1 GiB Git repository size guidance
MAX_PRIVATE_PAYLOAD_BYTES = 400*1024*1024  # cumulative, separate from Git overhead
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
    # Store the two exact official JSON byte streams in one compressed binary
    # frame. Base64 *inside* gzip needlessly inflates intermediate payloads.
    # Base64 exists only on GitHub's HTTP Contents API transport, not on disk.
    raw_kospi,raw_kosdaq=raw_by_market["KOSPI"],raw_by_market["KOSDAQ"]
    header=_canonical({"schema":PAIR_SCHEMA,"asof":day,
                       "lengths":[len(raw_kospi),len(raw_kosdaq)],
                       "hashes":[sha256(raw_kospi).hexdigest(),
                                 sha256(raw_kosdaq).hexdigest()]})
    frame=MAGIC+len(header).to_bytes(4,"big")+header+raw_kospi+raw_kosdaq
    packed=gzip.compress(frame,compresslevel=9,mtime=0)
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
        if not uncompressed.startswith(MAGIC) or len(uncompressed)<len(MAGIC)+4:
            raise ValueError()
        header_size=int.from_bytes(uncompressed[len(MAGIC):len(MAGIC)+4],"big")
        if not 20<header_size<1000:
            raise ValueError()
        start=len(MAGIC)+4
        header=json.loads(uncompressed[start:start+header_size])
        if (header.get("schema")!=PAIR_SCHEMA or header.get("asof")!=day
            or type(header.get("lengths")) is not list
            or len(header["lengths"])!=2
            or any(type(n) is not int or not 0<n<=20_000_000
                   for n in header["lengths"])):
            raise ValueError()
        first,second=header["lengths"]
        body=uncompressed[start+header_size:]
        if len(body)!=first+second:
            raise ValueError()
        raws={"KOSPI":body[:first],"KOSDAQ":body[first:]}
        if ([sha256(raws[x]).hexdigest() for x in ("KOSPI","KOSDAQ")]
            !=header.get("hashes")):
            raise ValueError()
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
        url=f"https://api.github.com/repos/{self.repository}"+("/"+path if path else "")
        headers={"Authorization":"Bearer "+self.token,
                 "Accept":"application/vnd.github+json",
                 "X-GitHub-Api-Version":"2022-11-28",
                 "User-Agent":"v3-private-history-research"}
        data=_canonical(body) if body is not None else None
        if body is not None:headers["Content-Type"]="application/json"
        req=request.Request(url,headers=headers,data=data,method=method)
        # Only read-only GETs may be retried. An ambiguous PUT failure could
        # already have committed the private original ZIP/checkpoint, and must
        # instead be reconciled by the caller's subsequent readback/resume.
        for attempt in range(3):
            try:
                with request.urlopen(req,timeout=30) as reply:
                    answer=reply.read(2_000_001)
                    if len(answer)>2_000_000:
                        raise PrivateHistoryError("private_api_response_too_large")
                    return json.loads(answer)
            except error.HTTPError as exc:
                if exc.code==404 and method=="GET" and path.startswith("contents/"):
                    return None
                # Never retry 401/403 permission blocks or generic 404.
                if method=="GET" and exc.code in (500,502,503,504) and attempt<2:
                    time.sleep(0.25*(attempt+1))
                    continue
                # A fixed, path-free diagnostic is safe for public CI logs.
                # Never expose the private URL, request/response body, headers,
                # token, or GitHub's potentially sensitive error message.
                issue=PrivateHistoryError("private_storage_api_failed")
                issue.safe_http_operation=(
                    "read_repository_metadata" if method=="GET" and not path
                    else "read_private_content" if method=="GET"
                    else "write_private_content" if method=="PUT"
                    else "other_private_operation")
                response_headers=exc.headers or {}
                if exc.code==403 and (response_headers.get("Retry-After") is not None
                                      or response_headers.get("X-RateLimit-Remaining")=="0"):
                    issue.safe_http_status="http_403_rate_limited"
                elif exc.code in (401,403,404,409,422,429,500,502,503,504):
                    issue.safe_http_status=f"http_{exc.code}"
                else:
                    issue.safe_http_status="http_other"
                raise issue from None
            except PrivateHistoryError:
                # Size/validation circuit breakers are not transport glitches.
                raise
            except (ValueError,TimeoutError,OSError):
                if method=="GET" and attempt<2:
                    time.sleep(0.25*(attempt+1))
                    continue
                issue=PrivateHistoryError("private_storage_api_failed")
                issue.safe_http_operation=(
                    "read_repository_metadata" if method=="GET" and not path
                    else "read_private_content" if method=="GET"
                    else "write_private_content" if method=="PUT"
                    else "other_private_operation")
                issue.safe_http_status="transport_or_response"
                raise issue from None

    def verify_private(self):
        info=self.transport("GET","")
        if (type(info) is not dict or info.get("private") is not True
            or str(info.get("full_name","")).casefold()!=self.repository.casefold()):
            raise PrivateHistoryError("target_repo_not_confirmed_private")
        # GitHub's size estimate is not a billing meter or instantaneous value.
        # It is nonetheless useful as an additional fail-safe circuit breaker.
        reported_kib=info.get("size")
        if type(reported_kib) is not int or reported_kib<0:
            raise PrivateHistoryError("private_repo_size_not_reported")
        if reported_kib>=MAX_REPO_SIZE_KIB:
            raise PrivateHistoryError("private_repository_budget_exceeded")
        return True

    def get_content(self,path):
        resp=self.transport("GET","contents/"+path)
        if resp is None:return None,None
        if (type(resp) is not dict or resp.get("type")!="file"
            or resp.get("encoding")!="base64" or type(resp.get("sha")) is not str):
            raise PrivateHistoryError("untrusted_private_blob")
        try:return base64.b64decode("".join(resp["content"].split()),validate=True),resp["sha"]
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
    """A constant-size checkpoint, not O(ten-year-days) saved JSON history.

    Legacy growing manifests require an explicit offline migration and are
    deliberately not silently upgraded or overwritten.
    """
    empty={"schema":SCHEMA,"start":START,"end":END,"next_index":0,
           "last_day":None,"last_pair_sha256":None,
           "compressed_bytes":0,"raw_bytes":0}
    if raw is None:
        return empty
    try:
        obj=json.loads(raw)
        i=obj["next_index"]
        if (type(obj) is not dict or obj.get("schema")!=SCHEMA
            or obj.get("start")!=START or obj.get("end")!=END
            or type(i) is not int or not 0<=i<=len(days)
            or type(obj.get("compressed_bytes")) is not int
            or not 0<=obj["compressed_bytes"]<=MAX_PRIVATE_PAYLOAD_BYTES
            or type(obj.get("raw_bytes")) is not int
            or obj["raw_bytes"]<obj["compressed_bytes"]
            or obj["raw_bytes"]>50_000_000_000):
            raise ValueError()
        if i==0:
            if obj["last_day"] is not None or obj["last_pair_sha256"] is not None:
                raise ValueError()
        elif (obj.get("last_day")!=days[i-1]
              or type(obj.get("last_pair_sha256")) is not str
              or not re.fullmatch(r"[a-f0-9]{64}",obj["last_pair_sha256"])):
            raise ValueError()
        return obj
    except Exception:
        raise PrivateHistoryError("invalid_or_legacy_private_checkpoint") from None


def _verify_checkpoint_tail(storage,days,manifest):
    """Catch missing/corrupt last committed day BEFORE calling KRX."""
    count=manifest["next_index"]
    if not count:
        return
    day=days[count-1]
    current,_=storage.get_content(_pair_path(day))
    if (current is None or sha256(current).hexdigest()!=manifest["last_pair_sha256"]):
        raise PrivateHistoryError("private_checkpoint_missing_or_mismatched_day")
    _unpack_pair(day,current)


def run(*,storage,client,dates,limit=MAX_DAYS):
    """Immutable private blobs + ONE tiny checkpoint commit per batch.

    A failed batch leaves only immutable orphan day blobs. The next batch
    revalidates and adopts them without repeated provider requests. The
    checkpoint is written only AFTER the complete requested cohort succeeds.
    """
    if type(limit) is not int or not 1<=limit<=MAX_DAYS:
        raise PrivateHistoryError("invalid_daily_limit")
    if (type(dates) is not list or dates!=sorted(set(dates))
        or not 5<=len(dates)<=6000):
        raise PrivateHistoryError("invalid_candidate_calendar")
    storage.verify_private()
    raw,checkpoint_sha=storage.get_content("krx/manifest.json")
    checkpoint=_check_manifest(raw,dates)
    _verify_checkpoint_tail(storage,dates,checkpoint)
    index=checkpoint["next_index"]
    window=dates[index:index+limit]
    new_calls=0;resumed=0;compressed_this_run=0;raw_this_run=0
    last_digest=checkpoint["last_pair_sha256"]
    if not window:
        return {
            "schema":"v3-krx-private-backfill-coverage-0.2",
            "status":"private_historical_source_stored",
            "total_candidate_days":len(dates),
            "durably_archived_pair_days":index,
            "remaining_candidate_days":0,
            "new_official_market_calls":0,
            "orphaned_private_days_reconciled":0,
            "compressed_private_source_bytes":checkpoint["compressed_bytes"],
            "raw_private_source_bytes":checkpoint["raw_bytes"],
            "source_compression_saving_pct":round(
                100*(1-checkpoint["compressed_bytes"]/checkpoint["raw_bytes"]),2)
                if checkpoint["raw_bytes"] else 0,
            "manifest_entry_count":1,
            "historical_adjusted_prices_certified":False,
            "official_trading_calendar_independently_verified":False,
            "market_raw_published":False,"ias_flow_scored":False,
        }
    for day in window:
        path=_pair_path(day)
        prior,_=storage.get_content(path)
        if prior is not None:
            meta=_unpack_pair(day,prior)
            blob=prior
            resumed+=1
        else:
            # Both long-term private-file budget and reported Git repo cap
            # are checked BEFORE a provider network call whenever possible.
            if (checkpoint["compressed_bytes"]+compressed_this_run+MAX_REMOTE_BYTES
                >MAX_PRIVATE_PAYLOAD_BYTES):
                raise PrivateHistoryError("private_compressed_budget_limit")
            raws={}
            for market in ("KOSPI","KOSDAQ"):
                try:
                    rows,returned=client.daily_market(market,day)
                    if json.loads(returned)["OutBlock_1"]!=rows:
                        raise ValueError()
                    raws[market]=returned
                except Exception:
                    raise PrivateHistoryError("official_krx_market_request_or_shape_failed") from None
                new_calls+=1
            blob,meta=_pack_pair(day,raws)
            storage.put_new(path,blob,"Store private observed KRX two-market "+day)
        compressed_this_run+=len(blob)
        raw_this_run+=sum(x["raw_bytes"] for x in meta.values())
        if checkpoint["compressed_bytes"]+compressed_this_run>MAX_PRIVATE_PAYLOAD_BYTES:
            raise PrivateHistoryError("private_compressed_budget_limit")
        index+=1
        last_digest=sha256(blob).hexdigest()
    compact=_canonical({
        "schema":SCHEMA,"start":START,"end":END,
        "next_index":index,"last_day":dates[index-1],
        "last_pair_sha256":last_digest,
        "compressed_bytes":checkpoint["compressed_bytes"]+compressed_this_run,
        "raw_bytes":checkpoint["raw_bytes"]+raw_this_run,
    })
    # Only one replacement of a constant-size pointer, not five growing
    # snapshots of the previous 2,500 days' SHA lists on each batch.
    if checkpoint_sha is None:
        storage.put_new("krx/manifest.json",compact,
                        "Create bounded private KRX checkpoint")
    else:
        storage.replace_manifest(compact,checkpoint_sha)
    written,_=storage.get_content("krx/manifest.json")
    if written!=compact:
        raise PrivateHistoryError("private_checkpoint_not_durably_verified")
    total_compressed=checkpoint["compressed_bytes"]+compressed_this_run
    total_raw=checkpoint["raw_bytes"]+raw_this_run
    return {
        "schema":"v3-krx-private-backfill-coverage-0.2",
        "status":"private_historical_source_stored",
        "total_candidate_days":len(dates),
        "durably_archived_pair_days":index,
        "remaining_candidate_days":len(dates)-index,
        "new_official_market_calls":new_calls,
        "orphaned_private_days_reconciled":resumed,
        "compressed_private_source_bytes":total_compressed,
        "raw_private_source_bytes":total_raw,
        "source_compression_saving_pct":round(
            100*(1-total_compressed/total_raw),2) if total_raw else 0,
        "manifest_entry_count":1,
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
        # Lasting KRX cloud retention requires explicit written clarification.
        # A previously advised boolean toggle alone is NOT legal approval.
        from v3_krx_permission_gate import (
            PermissionGateError, confirm_operator_approval)
        try:
            confirm_operator_approval(os.environ)
        except PermissionGateError:
            print(json.dumps({"status":"blocked",
                              "reason":"official_krx_cloud_storage_permission_unconfirmed",
                              "provider_calls":0,
                              "private_archive_files_written":0}))
            return 0
        if (not os.getenv("KRX_API_KEY")
            or not os.getenv("V3_PRIVATE_ARCHIVE_REPO")
            or not os.getenv("V3_PRIVATE_ARCHIVE_TOKEN")):
            print(json.dumps({"status":"blocked",
                              "reason":"private_archive_storage_or_api_credentials_not_configured",
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
