import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from db import BASE_DIR, db_conn, init_db

NPT = ZoneInfo("Asia/Kathmandu")
API_CACHE_CONTROL = os.getenv(
    "API_CACHE_CONTROL", "public, s-maxage=15, stale-while-revalidate=30"
)
STALE_SECONDS = int(os.getenv("STALE_THRESHOLD_SECONDS", "600"))
DEFAULT_ELECTION_ID = os.getenv("DEFAULT_ELECTION_ID", "2082")
DEFAULT_PREVIOUS_MAP = {
    "2082": "2079",
}

EKANTIPUR_ASSETS_DIR = BASE_DIR / "data" / "elections" / "ekantipur_2082" / "assets"

app = FastAPI(title="Nepal Election API", version="0.2.0")

if EKANTIPUR_ASSETS_DIR.exists():
    app.mount(
        "/assets/ekantipur_2082",
        StaticFiles(directory=str(EKANTIPUR_ASSETS_DIR)),
        name="ekantipur_assets",
    )


@app.on_event("startup")
def startup_event() -> None:
    init_db()


def _photo_url(raw_path: Optional[str]) -> Optional[str]:
    """Convert a stored photo_path like backend/data/.../assets/candidates/1/1.jpg to a URL path."""
    if not raw_path:
        return None
    prefix = "backend/data/elections/ekantipur_2082/assets/"
    if raw_path.startswith(prefix):
        return "/assets/ekantipur_2082/" + raw_path[len(prefix):]
    suffix = "assets/"
    idx = raw_path.find("assets/")
    if idx >= 0:
        return "/assets/ekantipur_2082/" + raw_path[idx + len("assets/"):]
    return None


origins_raw = os.getenv("CORS_ALLOW_ORIGINS", "*")
allow_origins = [origin.strip() for origin in origins_raw.split(",") if origin.strip()]
if not allow_origins:
    allow_origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": "Nepal Election API",
        "status": "ok",
        "docs": "/docs",
        "elections": "/api/elections",
        "meta": "/api/meta?election_id=2082",
        "results": "/api/results?election_id=2082&page=1&page_size=10",
        "winners": "/api/winners?election_id=2079",
        "candidates": "/api/candidates?election_id=2082&page=1&page_size=24",
        "candidate_detail": "/api/candidates/{profile_id}",
        "parties_2082": "/api/parties?election_id=2082",
    }


@app.middleware("http")
async def add_cache_headers(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", API_CACHE_CONTROL)
    return response


def _etag(payload: Union[Dict[str, Any], List[Any]]) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_npt(ts: Optional[str]) -> Optional[str]:
    dt = _parse_iso(ts)
    if not dt:
        return None
    return dt.astimezone(NPT).strftime("%Y-%m-%d %H:%M:%S NPT")


def _age_seconds(ts: Optional[str]) -> Optional[int]:
    dt = _parse_iso(ts)
    if not dt:
        return None
    return int((datetime.now(timezone.utc) - dt).total_seconds())


def _window_to_timedelta(window: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([mh])", window.strip().lower())
    if not match:
        return timedelta(hours=6)
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return timedelta(minutes=value)
    return timedelta(hours=value)


def _bucket_5m(ts: str) -> Optional[str]:
    dt = _parse_iso(ts)
    if not dt:
        return None
    minute = (dt.minute // 5) * 5
    bucket = dt.replace(minute=minute, second=0, microsecond=0)
    return bucket.isoformat().replace("+00:00", "Z")


def _resolved_previous_election(current: str, previous: Optional[str]) -> Optional[str]:
    if previous == "none":
        return None
    if previous:
        return previous
    return DEFAULT_PREVIOUS_MAP.get(current)


@app.get("/api/elections")
def api_elections(response: Response):
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT election_id, COUNT(*) AS rows_count, MAX(updated_ts) AS latest_row_ts
            FROM results_latest
            GROUP BY election_id
            ORDER BY election_id DESC
            """
        ).fetchall()

    payload = {
        "default": DEFAULT_ELECTION_ID,
        "items": [dict(row) for row in rows],
    }
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/meta")
def api_meta(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
):
    with db_conn() as conn:
        row_count = conn.execute(
            "SELECT COUNT(*) AS c FROM results_latest WHERE election_id = ?",
            (election_id,),
        ).fetchone()["c"]

        endpoints = conn.execute(
            """
            SELECT url, election_id, last_seen_ts, last_changed_ts, last_status, last_http_code, last_error
            FROM endpoints
            WHERE election_id = ?
            ORDER BY id ASC
            """,
            (election_id,),
        ).fetchall()

        central = conn.execute(
            """
            SELECT url, last_seen_ts, last_changed_ts
            FROM endpoints
            WHERE election_id = ? AND url LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (election_id, "%ElectionResultCentral" + election_id + ".txt%"),
        ).fetchone()

        last_polled_at = None
        if endpoints:
            last_polled_at = max((item["last_seen_ts"] for item in endpoints if item["last_seen_ts"]), default=None)

        results_updated_at = None
        if central and central["last_changed_ts"]:
            results_updated_at = central["last_changed_ts"]
        elif endpoints:
            results_updated_at = max(
                (item["last_changed_ts"] for item in endpoints if item["last_changed_ts"]),
                default=None,
            )

        age_seconds = _age_seconds(results_updated_at)
        freshness_status = "stale" if age_seconds is None or age_seconds > STALE_SECONDS else "fresh"

        if row_count == 0 and results_updated_at is None:
            feed_state = "empty"
        elif freshness_status == "stale":
            feed_state = "stale"
        else:
            feed_state = "updating"

        parse_errors = conn.execute(
            "SELECT COALESCE(SUM(errors), 0) AS e FROM ingest_runs WHERE election_id = ?",
            (election_id,),
        ).fetchone()["e"]

        payload = {
            "election_id": election_id,
            "results_updated_at": results_updated_at,
            "results_updated_at_npt": _to_npt(results_updated_at),
            "last_polled_at": last_polled_at,
            "last_polled_at_npt": _to_npt(last_polled_at),
            "results_age_seconds": age_seconds,
            "freshness_status": freshness_status,
            "feed_state": feed_state,
            "rows_count": row_count,
            "parse_error_count": parse_errors,
            "endpoints": [dict(row) for row in endpoints],
        }

    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/results")
def api_results(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
    previous_election_id: Optional[str] = Query(default=None),
    state_id: Optional[str] = None,
    district_id: Optional[str] = None,
    constituency_id: Optional[str] = None,
    constituency_key: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
):
    where = ["r.election_id = ?"]
    params: List[Union[str, int]] = [election_id]

    if state_id:
        where.append("r.state_id = ?")
        params.append(state_id)
    if district_id:
        where.append("r.district_id = ?")
        params.append(district_id)
    if constituency_id:
        where.append("r.constituency_id = ?")
        params.append(constituency_id)
    if constituency_key:
        where.append("r.constituency_key = ?")
        params.append(constituency_key)
    if q:
        where.append(
            "(" + " OR ".join(
                [
                    "r.candidate LIKE ?",
                    "r.party LIKE ?",
                    "r.district_name LIKE ?",
                    "r.constituency_name LIKE ?",
                ]
            ) + ")"
        )
        like = "%" + q.strip() + "%"
        params.extend([like, like, like, like])

    where_sql = "WHERE " + " AND ".join(where)
    offset = (page - 1) * page_size
    resolved_prev = _resolved_previous_election(election_id, previous_election_id)

    with db_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM results_latest r " + where_sql,
            params,
        ).fetchone()["c"]

        if resolved_prev:
            rows = conn.execute(
                """
                SELECT
                    r.*,
                    w.party AS prev_winner_party,
                    w.candidate AS prev_winner_candidate,
                    w.party_symbol_name AS prev_winner_symbol_name,
                    w.votes AS prev_winner_votes
                FROM results_latest r
                LEFT JOIN constituency_winners w
                  ON w.election_id = ?
                 AND w.constituency_key = r.constituency_key
                """
                + where_sql +
                """
                ORDER BY COALESCE(r.votes, -1) DESC, r.candidate ASC
                LIMIT ? OFFSET ?
                """,
                [resolved_prev] + params + [page_size, offset],
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    r.*,
                    NULL AS prev_winner_party,
                    NULL AS prev_winner_candidate,
                    NULL AS prev_winner_symbol_name,
                    NULL AS prev_winner_votes
                FROM results_latest r
                """
                + where_sql +
                """
                ORDER BY COALESCE(r.votes, -1) DESC, r.candidate ASC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ).fetchall()

        payload = {
            "election_id": election_id,
            "previous_election_id": resolved_prev,
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/party")
def api_party(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
):
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT election_id, party, party_symbol_name, elected_count, leading_count, total_votes, updated_ts
            FROM party_agg_by_election
            WHERE election_id = ?
            ORDER BY total_votes DESC
            """,
            (election_id,),
        ).fetchall()
        payload = {"election_id": election_id, "items": [dict(row) for row in rows]}
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/lookups/states")
def api_states(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
):
    with db_conn() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT COALESCE(state_id, '') AS id, COALESCE(state_name, state_id, '') AS name
            FROM results_latest
            WHERE election_id = ? AND COALESCE(TRIM(state_id), '') <> ''
            ORDER BY name ASC
            """,
            (election_id,),
        ).fetchall()
        payload = {"election_id": election_id, "items": [dict(row) for row in rows]}
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/lookups/districts")
def api_districts(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
    state_id: Optional[str] = None,
):
    with db_conn() as conn:
        params: List[Any] = [election_id]
        scoped_where = ["election_id = ?", "COALESCE(TRIM(district_id), '') <> ''"]
        if state_id:
            scoped_where.append("state_id = ?")
            params.append(state_id)

        rows = conn.execute(
            """
            WITH scoped AS (
                SELECT DISTINCT district_id
                FROM results_latest
                WHERE """
            + " AND ".join(scoped_where)
            + """
            )
            SELECT
                s.district_id AS id,
                MAX(CASE WHEN r.election_id = ? THEN r.district_name END) AS name,
                MAX(CASE WHEN r.district_name GLOB '*[A-Za-z]*' THEN r.district_name END) AS name_en,
                MAX(CASE WHEN r.election_id = ? THEN r.state_id END) AS state_id
            FROM scoped s
            JOIN results_latest r ON r.district_id = s.district_id
            GROUP BY s.district_id
            ORDER BY COALESCE(name_en, name) ASC
            """,
            params + [election_id, election_id],
        ).fetchall()
        payload = {"election_id": election_id, "items": [dict(row) for row in rows]}
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/lookups/constituencies")
def api_constituencies(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
    district_id: Optional[str] = None,
    state_id: Optional[str] = None,
):
    with db_conn() as conn:
        params: List[Any] = [election_id]
        where = [
            "election_id = ?",
            "COALESCE(TRIM(constituency_id), '') <> ''",
            "COALESCE(TRIM(constituency_key), '') <> ''",
        ]
        if district_id:
            where.append("district_id = ?")
            params.append(district_id)
        if state_id:
            where.append("state_id = ?")
            params.append(state_id)

        rows = conn.execute(
            """
            SELECT
                constituency_key AS key,
                constituency_id AS id,
                constituency_name AS name,
                district_id,
                district_name
            FROM results_latest
            WHERE """
            + " AND ".join(where)
            + """
            GROUP BY constituency_key, constituency_id, constituency_name, district_id, district_name
            ORDER BY district_name ASC, CAST(constituency_id AS INTEGER) ASC, constituency_name ASC
            """,
            params,
        ).fetchall()
        payload = {"election_id": election_id, "items": [dict(row) for row in rows]}
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/winners")
def api_winners(
    response: Response,
    election_id: str = Query(default="2079"),
    state_id: Optional[str] = None,
    district_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
):
    """Constituency winners for an election (e.g. 2079). Used when 2082 has no results yet."""
    with db_conn() as conn:
        where = ["election_id = ?"]
        params: List[Any] = [election_id]
        if district_id:
            where.append("district_id = ?")
            params.append(district_id)
        if state_id:
            where.append(
                "district_id IN (SELECT id FROM lookups_district WHERE state_id = ?)"
            )
            params.append(state_id)

        where_sql = " AND ".join(where)
        count_row = conn.execute(
            f"SELECT COUNT(*) AS c FROM constituency_winners WHERE {where_sql}",
            params,
        ).fetchone()
        total = count_row["c"]

        offset = (page - 1) * page_size
        rows = conn.execute(
            f"""
            SELECT
                constituency_key,
                constituency_id,
                constituency_name,
                district_id,
                district_name,
                candidate,
                party,
                party_symbol_name,
                votes,
                status,
                updated_ts
            FROM constituency_winners
            WHERE {where_sql}
            ORDER BY district_name ASC, constituency_name ASC, constituency_key ASC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        ).fetchall()

        payload = {
            "election_id": election_id,
            "items": [dict(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/analytics/summary")
def api_analytics_summary(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
):
    with db_conn() as conn:
        totals = conn.execute(
            """
            SELECT
                COUNT(*) AS rows_count,
                COUNT(DISTINCT party) AS parties_count,
                COUNT(DISTINCT district_id) AS districts_count,
                COUNT(DISTINCT constituency_key) AS constituencies_count,
                COALESCE(SUM(votes), 0) AS total_votes
            FROM results_latest
            WHERE election_id = ?
            """,
            (election_id,),
        ).fetchone()

        top_parties = conn.execute(
            """
            SELECT party, party_symbol_name, total_votes, elected_count, leading_count
            FROM party_agg_by_election
            WHERE election_id = ?
            ORDER BY total_votes DESC
            LIMIT 10
            """,
            (election_id,),
        ).fetchall()

        snapshots_1h = conn.execute(
            "SELECT COUNT(*) AS c FROM snapshots WHERE election_id = ? AND ts >= ?",
            (
                election_id,
                (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            ),
        ).fetchone()["c"]

        snapshots_24h = conn.execute(
            "SELECT COUNT(*) AS c FROM snapshots WHERE election_id = ? AND ts >= ?",
            (
                election_id,
                (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            ),
        ).fetchone()["c"]

        payload = {
            "election_id": election_id,
            "rows_count": totals["rows_count"],
            "parties_count": totals["parties_count"],
            "districts_count": totals["districts_count"],
            "constituencies_count": totals["constituencies_count"],
            "total_votes": totals["total_votes"],
            "updates_last_1h": snapshots_1h,
            "updates_last_24h": snapshots_24h,
            "top_parties": [dict(row) for row in top_parties],
        }

    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/analytics/timeseries")
def api_analytics_timeseries(
    response: Response,
    election_id: str = Query(default=DEFAULT_ELECTION_ID),
    metric: str = Query(default="changes", pattern="^(changes|ingest_errors)$"),
    window: str = Query(default="24h"),
):
    delta = _window_to_timedelta(window)
    cutoff = datetime.now(timezone.utc) - delta
    cutoff_iso = cutoff.isoformat().replace("+00:00", "Z")

    buckets: Dict[str, int] = {}

    with db_conn() as conn:
        if metric == "changes":
            rows = conn.execute(
                "SELECT ts FROM snapshots WHERE election_id = ? AND ts >= ? ORDER BY ts ASC",
                (election_id, cutoff_iso),
            ).fetchall()
            for row in rows:
                bucket = _bucket_5m(row["ts"])
                if not bucket:
                    continue
                buckets[bucket] = buckets.get(bucket, 0) + 1
        else:
            rows = conn.execute(
                """
                SELECT started_ts, errors
                FROM ingest_runs
                WHERE election_id = ? AND started_ts >= ?
                ORDER BY started_ts ASC
                """,
                (election_id, cutoff_iso),
            ).fetchall()
            for row in rows:
                bucket = _bucket_5m(row["started_ts"])
                if not bucket:
                    continue
                buckets[bucket] = buckets.get(bucket, 0) + int(row["errors"] or 0)

    points = [{"ts": key, "value": buckets[key]} for key in sorted(buckets.keys())]
    payload = {"election_id": election_id, "metric": metric, "window": window, "points": points}
    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/analytics/compare")
def api_analytics_compare(
    response: Response,
    current: str = Query(default=DEFAULT_ELECTION_ID),
    previous: str = Query(default="2079"),
):
    if previous == "none":
        payload = {
            "current": current,
            "previous": None,
            "party_deltas": [],
            "constituency_flips": [],
            "retained_constituencies": 0,
        }
        response.headers["ETag"] = _etag(payload)
        return payload

    with db_conn() as conn:
        curr_rows = conn.execute(
            """
            SELECT party, COALESCE(total_votes, 0) AS total_votes,
                   COALESCE(elected_count, 0) AS elected_count,
                   COALESCE(leading_count, 0) AS leading_count
            FROM party_agg_by_election
            WHERE election_id = ?
            """,
            (current,),
        ).fetchall()
        prev_rows = conn.execute(
            """
            SELECT party, COALESCE(total_votes, 0) AS total_votes,
                   COALESCE(elected_count, 0) AS elected_count,
                   COALESCE(leading_count, 0) AS leading_count
            FROM party_agg_by_election
            WHERE election_id = ?
            """,
            (previous,),
        ).fetchall()

        curr_map = {row["party"]: dict(row) for row in curr_rows}
        prev_map = {row["party"]: dict(row) for row in prev_rows}

        parties = sorted(set(curr_map.keys()) | set(prev_map.keys()))
        party_deltas = []
        for party in parties:
            c = curr_map.get(party, {"total_votes": 0, "elected_count": 0, "leading_count": 0})
            p = prev_map.get(party, {"total_votes": 0, "elected_count": 0, "leading_count": 0})
            party_deltas.append(
                {
                    "party": party,
                    "current_votes": c["total_votes"],
                    "previous_votes": p["total_votes"],
                    "vote_delta": c["total_votes"] - p["total_votes"],
                    "current_elected": c["elected_count"],
                    "previous_elected": p["elected_count"],
                    "elected_delta": c["elected_count"] - p["elected_count"],
                }
            )

        flips = conn.execute(
            """
            SELECT
                c.constituency_key,
                COALESCE(c.constituency_name, c.constituency_id) AS constituency,
                c.party AS current_party,
                p.party AS previous_party,
                c.candidate AS current_candidate,
                p.candidate AS previous_candidate
            FROM constituency_winners c
            JOIN constituency_winners p
              ON p.constituency_key = c.constituency_key
             AND p.election_id = ?
            WHERE c.election_id = ?
              AND COALESCE(c.party, '') <> COALESCE(p.party, '')
            ORDER BY constituency ASC
            """,
            (previous, current),
        ).fetchall()

        retained_count = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM constituency_winners c
            JOIN constituency_winners p
              ON p.constituency_key = c.constituency_key
             AND p.election_id = ?
            WHERE c.election_id = ?
              AND COALESCE(c.party, '') = COALESCE(p.party, '')
            """,
            (previous, current),
        ).fetchone()["c"]

    payload = {
        "current": current,
        "previous": previous,
        "party_deltas": sorted(party_deltas, key=lambda x: x["vote_delta"], reverse=True),
        "constituency_flips": [dict(row) for row in flips],
        "retained_constituencies": retained_count,
    }
    response.headers["ETag"] = _etag(payload)
    return payload


def _enrich_candidate(row: Any) -> Dict[str, Any]:
    """Convert a candidates_profiles_2082 row into a JSON-safe dict with a photo URL."""
    d = dict(row)
    d["photo_url"] = _photo_url(d.get("photo_path"))
    for field in ("political_history", "parliament_tour", "extra_json"):
        raw = d.get(field)
        if raw:
            try:
                d[field] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
    return d


@app.get("/api/candidates")
def api_candidates(
    response: Response,
    election_id: str = Query(default="2082"),
    q: Optional[str] = Query(default=None, description="Search name or party"),
    party_id: Optional[int] = Query(default=None),
    province: Optional[str] = Query(default=None),
    district: Optional[str] = Query(default=None),
    constituency: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=200),
):
    """2082 candidate listing with optional search/filters."""
    with db_conn() as conn:
        # Check if profiles table has rows; if not, fall back to results_latest.
        profile_count = conn.execute("SELECT COUNT(*) AS c FROM candidates_profiles_2082").fetchone()["c"]

        if profile_count > 0:
            where = []
            params: List[Any] = []

            if q:
                like = "%" + q.strip() + "%"
                where.append("(candidate_name LIKE ? OR candidate_name_nep LIKE ? OR party_name LIKE ? OR constituency LIKE ?)")
                params.extend([like, like, like, like])
            if party_id is not None:
                where.append("party_id = ?")
                params.append(party_id)
            if province:
                where.append("province LIKE ?")
                params.append("%" + province.strip() + "%")
            if district:
                where.append("district LIKE ?")
                params.append("%" + district.strip() + "%")
            if constituency:
                where.append("constituency LIKE ?")
                params.append("%" + constituency.strip() + "%")

            where_sql = ("WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM candidates_profiles_2082 {where_sql}", params
            ).fetchone()["c"]

            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT
                    p.profile_id,
                    p.candidate_name,
                    p.candidate_name_nep,
                    p.party_id,
                    p.party_name,
                    p.party_name_nep,
                    p.party_symbol_url,
                    p.constituency,
                    p.district,
                    p.province,
                    p.birth_date,
                    p.address,
                    p.election_system,
                    p.photo_path,
                    p.updated_ts,
                    r.votes,
                    r.status,
                    r.rank
                FROM candidates_profiles_2082 p
                LEFT JOIN (
                    SELECT candidate, constituency_name, votes, status, rank
                    FROM results_latest
                    WHERE election_id = '{election_id}'
                      AND source_endpoint != 'ekantipur_2082'
                      AND votes IS NOT NULL
                ) r ON LOWER(r.candidate) = LOWER(p.candidate_name)
                      AND LOWER(COALESCE(r.constituency_name,'')) = LOWER(COALESCE(p.constituency,''))
                {where_sql}
                ORDER BY COALESCE(r.votes, -1) DESC, p.candidate_name ASC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ).fetchall()

            items = []
            for row in rows:
                d = dict(row)
                d["photo_url"] = _photo_url(d.get("photo_path"))
                items.append(d)
        else:
            # Fallback: derive from results_latest for this election_id.
            where = ["election_id = ?", "source_endpoint = 'ekantipur_2082'"]
            params = [election_id]
            if q:
                like = "%" + q.strip() + "%"
                where.append("(candidate LIKE ? OR party LIKE ? OR district_name LIKE ? OR constituency_name LIKE ?)")
                params.extend([like, like, like, like])
            if province:
                where.append("state_name LIKE ?")
                params.append("%" + province.strip() + "%")
            if district:
                where.append("district_name LIKE ?")
                params.append("%" + district.strip() + "%")
            where_sql = "WHERE " + " AND ".join(where)

            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM results_latest {where_sql}", params
            ).fetchone()["c"]

            offset = (page - 1) * page_size
            rows = conn.execute(
                f"""
                SELECT id, candidate AS candidate_name, party AS party_name,
                       district_name AS district, constituency_name AS constituency,
                       state_name AS province, source_json
                FROM results_latest
                {where_sql}
                ORDER BY candidate ASC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ).fetchall()

            items = []
            for row in rows:
                d = dict(row)
                src = {}
                if d.get("source_json"):
                    try:
                        src = json.loads(d["source_json"])
                    except Exception:
                        pass
                profile = src.get("profile") or {}
                d["photo_url"] = _photo_url(profile.get("photo_path"))
                d.pop("source_json", None)
                items.append(d)

        payload = {
            "election_id": election_id,
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/candidates/{profile_id}")
def api_candidate_detail(
    profile_id: int,
    response: Response,
):
    """Full profile for a single candidate by profile_id."""
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM candidates_profiles_2082 WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Candidate not found")

        payload = _enrich_candidate(row)

    response.headers["ETag"] = _etag(payload)
    return payload


@app.get("/api/parties")
def api_parties_2082(
    response: Response,
    election_id: str = Query(default="2082"),
):
    """List of parties for a given election_id with candidate counts."""
    with db_conn() as conn:
        profile_count = conn.execute("SELECT COUNT(*) AS c FROM candidates_profiles_2082").fetchone()["c"]

        if profile_count > 0 and election_id == "2082":
            rows = conn.execute(
                """
                SELECT
                    party_id,
                    party_name,
                    party_name_nep,
                    party_symbol_url,
                    COUNT(*) AS candidate_count
                FROM candidates_profiles_2082
                WHERE party_name IS NOT NULL
                GROUP BY party_id, party_name, party_name_nep, party_symbol_url
                ORDER BY candidate_count DESC, party_name ASC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    party AS party_name,
                    NULL AS party_name_nep,
                    NULL AS party_symbol_url,
                    NULL AS party_id,
                    COUNT(*) AS candidate_count
                FROM results_latest
                WHERE election_id = ? AND party IS NOT NULL
                GROUP BY party
                ORDER BY candidate_count DESC, party ASC
                """,
                (election_id,),
            ).fetchall()

        payload = {
            "election_id": election_id,
            "items": [dict(row) for row in rows],
        }

    response.headers["ETag"] = _etag(payload)
    return payload
