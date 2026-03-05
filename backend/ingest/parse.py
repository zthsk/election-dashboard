import json
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from db import utc_now_iso

LOOKUP_ID_KEYS = ["id", "ID", "StateID", "DistrictID", "Id", "code", "Code"]
LOOKUP_NAME_KEYS = ["name", "Name", "StateName", "DistrictName", "DistName", "title"]
STATE_PARENT_KEYS = ["ParentID", "StateID", "ProvinceID", "parent_id"]


def infer_election_id(endpoint_url: str) -> str:
    match = re.search(r"(20\d{2})", endpoint_url)
    if match:
        return match.group(1)
    return "2082"


def _decode(content: bytes) -> Any:
    text = content.decode("utf-8-sig", errors="replace")
    return json.loads(text)


def _records(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "rows", "value"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                return [row for row in candidate if isinstance(row, dict)]
        return [payload]
    return []


def _pick(record: Dict[str, Any], aliases: List[str]) -> Any:
    for key in aliases:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    cleaned = str(value).replace(",", "").strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    value = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _is_elected(status: Optional[str]) -> int:
    if not status:
        return 0
    s = status.lower()
    return 1 if ("elected" in s or "won" in s) else 0


def _is_leading(status: Optional[str]) -> int:
    if not status:
        return 0
    s = status.lower()
    return 1 if ("leading" in s or "ahead" in s) else 0


def _normalize_key(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    key = re.sub(r"[^a-z0-9]+", "", value.lower())
    return key or None


def _constituency_key(
    constituency_id: Optional[str],
    constituency_name: Optional[str],
    district_id: Optional[str],
    district_name: Optional[str],
) -> Optional[str]:
    cid = _normalize_key(constituency_id)
    if cid:
        return "id:" + cid

    raw = "|".join(
        [
            district_id or "",
            district_name or "",
            constituency_name or "",
        ]
    )
    nkey = _normalize_key(raw)
    if nkey:
        return "name:" + nkey
    return None


def _upsert_states(conn: sqlite3.Connection, payload: Any) -> Dict[str, int]:
    records = _records(payload)
    processed = 0
    for record in records:
        state_id = _as_text(_pick(record, LOOKUP_ID_KEYS))
        state_name = _as_text(_pick(record, LOOKUP_NAME_KEYS))
        if not state_id or not state_name:
            continue
        conn.execute(
            """
            INSERT INTO lookups_state(id, name, extra_json)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                extra_json = excluded.extra_json
            """,
            (state_id, state_name, json.dumps(record, ensure_ascii=False)),
        )
        processed += 1
    return {"processed": processed}


def _upsert_districts(conn: sqlite3.Connection, payload: Any) -> Dict[str, int]:
    records = _records(payload)
    processed = 0
    for record in records:
        district_id = _as_text(_pick(record, LOOKUP_ID_KEYS))
        district_name = _as_text(_pick(record, LOOKUP_NAME_KEYS))
        state_id = _as_text(_pick(record, STATE_PARENT_KEYS))
        if not district_id or not district_name:
            continue
        conn.execute(
            """
            INSERT INTO lookups_district(id, name, state_id, extra_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                state_id = COALESCE(excluded.state_id, lookups_district.state_id),
                extra_json = excluded.extra_json
            """,
            (district_id, district_name, state_id, json.dumps(record, ensure_ascii=False)),
        )
        processed += 1
    return {"processed": processed}


def _recompute_party_agg(conn: sqlite3.Connection, election_id: str) -> None:
    now_ts = utc_now_iso()
    rows = conn.execute(
        """
        SELECT party, party_symbol_name, status, votes
        FROM results_latest
        WHERE election_id = ?
          AND COALESCE(TRIM(party), '') <> ''
        """,
        (election_id,),
    ).fetchall()

    agg: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        party = row["party"]
        if party not in agg:
            agg[party] = {
                "elected": 0,
                "leading": 0,
                "votes": 0,
                "symbol": row["party_symbol_name"],
            }
        agg[party]["elected"] += _is_elected(row["status"])
        agg[party]["leading"] += _is_leading(row["status"])
        agg[party]["votes"] += int(row["votes"] or 0)
        if not agg[party]["symbol"] and row["party_symbol_name"]:
            agg[party]["symbol"] = row["party_symbol_name"]

    conn.execute("DELETE FROM party_agg_by_election WHERE election_id = ?", (election_id,))
    for party, values in agg.items():
        conn.execute(
            """
            INSERT INTO party_agg_by_election(
                election_id, party, party_symbol_name, elected_count, leading_count, total_votes, updated_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                election_id,
                party,
                values["symbol"],
                values["elected"],
                values["leading"],
                values["votes"],
                now_ts,
            ),
        )


def _recompute_constituency_winners(conn: sqlite3.Connection, election_id: str) -> None:
    now_ts = utc_now_iso()
    rows = conn.execute(
        """
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
            rank
        FROM results_latest
        WHERE election_id = ?
          AND COALESCE(TRIM(constituency_key), '') <> ''
        ORDER BY constituency_key ASC, COALESCE(votes, -1) DESC, COALESCE(rank, 9999) ASC
        """,
        (election_id,),
    ).fetchall()

    best: Dict[str, sqlite3.Row] = {}
    for row in rows:
        key = row["constituency_key"]
        if not key:
            continue
        if key not in best:
            best[key] = row
            continue

        current = best[key]
        current_score = (_is_elected(current["status"]), int(current["votes"] or -1), -int(current["rank"] or 9999))
        row_score = (_is_elected(row["status"]), int(row["votes"] or -1), -int(row["rank"] or 9999))
        if row_score > current_score:
            best[key] = row

    conn.execute("DELETE FROM constituency_winners WHERE election_id = ?", (election_id,))
    for key, row in best.items():
        conn.execute(
            """
            INSERT INTO constituency_winners(
                election_id,
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
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                election_id,
                key,
                row["constituency_id"],
                row["constituency_name"],
                row["district_id"],
                row["district_name"],
                row["candidate"],
                row["party"],
                row["party_symbol_name"],
                row["votes"],
                row["status"],
                now_ts,
            ),
        )


def _replace_results(
    conn: sqlite3.Connection,
    election_id: str,
    endpoint_url: str,
    payload: Any,
) -> Dict[str, int]:
    records = _records(payload)
    now_ts = utc_now_iso()

    district_to_state = {
        row["id"]: row["state_id"]
        for row in conn.execute("SELECT id, state_id FROM lookups_district").fetchall()
    }

    alias = {
        "race_type": ["RaceType", "race_type", "ElectionType", "Category", "ElectionCategory"],
        "state_id": ["StateID", "state_id", "ProvinceID", "province_id"],
        "state_name": ["StateName", "state_name", "ProvinceName"],
        "district_id": ["DistrictID", "DistID", "district_id"],
        "district_name": ["DistName", "DistrictName", "district_name"],
        "constituency_id": ["ConstID", "SCConstID", "ConstituencyID", "constituency_id"],
        "constituency_name": ["ConstituencyName", "Constituency", "ConstName", "SCConstName"],
        "municipality": ["Municipality", "LocalLevel", "MunicipalityName"],
        "ward": ["Ward", "WardNo", "ward"],
        "candidate": ["CandidateName", "candidate", "candName"],
        "party": ["PartyName", "party", "Party", "PoliticalPartyName"],
        "party_symbol_id": ["SymbolID", "party_symbol_id", "PartySymbolID"],
        "party_symbol_name": ["SymbolName", "party_symbol_name", "PartySymbolName"],
        "votes": ["Vote", "votes", "TotalVote", "VoteCount"],
        "status": ["Status", "Result", "IsElected", "ElectionStatus"],
        "rank": ["Rank", "Position", "rank"],
        "current_address": ["CurrentAddress", "Address", "current_address"],
    }

    conn.execute(
        "DELETE FROM results_latest WHERE election_id = ? AND source_endpoint = ?",
        (election_id, endpoint_url),
    )

    inserted = 0
    discovered_states: Dict[str, str] = {}
    discovered_districts: Dict[str, Tuple[str, Optional[str]]] = {}

    for record in records:
        district_id = _as_text(_pick(record, alias["district_id"]))
        state_id = _as_text(_pick(record, alias["state_id"]))
        state_name = _as_text(_pick(record, alias["state_name"]))
        district_name = _as_text(_pick(record, alias["district_name"]))

        if not state_id and district_id:
            state_id = district_to_state.get(district_id)
        if state_id and state_name:
            discovered_states[state_id] = state_name
        if district_id and district_name:
            discovered_districts[district_id] = (district_name, state_id)

        constituency_id = _as_text(_pick(record, alias["constituency_id"]))
        constituency_name = _as_text(_pick(record, alias["constituency_name"]))
        constituency_key = _constituency_key(
            constituency_id,
            constituency_name,
            district_id,
            district_name,
        )

        values = {
            "race_type": _as_text(_pick(record, alias["race_type"])),
            "state_id": state_id,
            "state_name": state_name,
            "district_id": district_id,
            "district_name": district_name,
            "constituency_id": constituency_id,
            "constituency_name": constituency_name,
            "constituency_key": constituency_key,
            "municipality": _as_text(_pick(record, alias["municipality"])),
            "ward": _as_text(_pick(record, alias["ward"])),
            "candidate": _as_text(_pick(record, alias["candidate"])),
            "party": _as_text(_pick(record, alias["party"])),
            "party_symbol_id": _as_text(_pick(record, alias["party_symbol_id"])),
            "party_symbol_name": _as_text(_pick(record, alias["party_symbol_name"])),
            "votes": _as_int(_pick(record, alias["votes"])),
            "status": _as_text(_pick(record, alias["status"])),
            "rank": _as_int(_pick(record, alias["rank"])),
            "current_address": _as_text(_pick(record, alias["current_address"])),
        }

        conn.execute(
            """
            INSERT INTO results_latest(
                election_id, race_type, state_id, state_name, district_id, district_name,
                constituency_id, constituency_name, constituency_key, municipality, ward,
                candidate, party, party_symbol_id, party_symbol_name,
                votes, status, rank, current_address, source_endpoint, source_json, updated_ts
            )
            VALUES (
                :election_id, :race_type, :state_id, :state_name, :district_id, :district_name,
                :constituency_id, :constituency_name, :constituency_key, :municipality, :ward,
                :candidate, :party, :party_symbol_id, :party_symbol_name,
                :votes, :status, :rank, :current_address, :source_endpoint, :source_json, :updated_ts
            )
            """,
            {
                **values,
                "election_id": election_id,
                "source_endpoint": endpoint_url,
                "source_json": json.dumps(record, ensure_ascii=False),
                "updated_ts": now_ts,
            },
        )
        inserted += 1

    for sid, sname in discovered_states.items():
        conn.execute(
            """
            INSERT INTO lookups_state(id, name, extra_json)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name
            """,
            (sid, sname, None),
        )

    for did, (dname, sid) in discovered_districts.items():
        conn.execute(
            """
            INSERT INTO lookups_district(id, name, state_id, extra_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                state_id = COALESCE(excluded.state_id, lookups_district.state_id)
            """,
            (did, dname, sid, None),
        )

    _recompute_party_agg(conn, election_id)
    _recompute_constituency_winners(conn, election_id)
    return {"processed": inserted}


def parse_endpoint(
    conn: sqlite3.Connection,
    endpoint_url: str,
    content: bytes,
    snapshot_path: str,
    election_id: Optional[str] = None,
) -> Dict[str, Any]:
    started = utc_now_iso()
    resolved_election_id = election_id or infer_election_id(endpoint_url)

    try:
        payload = _decode(content)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "processed": 0,
            "error": "JSON decode error: %s" % str(exc),
            "snapshot_path": snapshot_path,
            "started": started,
        }

    try:
        lower_url = endpoint_url.lower()
        if endpoint_url.endswith("/states.json"):
            details = _upsert_states(conn, payload)
        elif endpoint_url.endswith("/districts.json"):
            details = _upsert_districts(conn, payload)
        elif "electionresult" in lower_url and lower_url.endswith(".txt"):
            details = _replace_results(conn, resolved_election_id, endpoint_url, payload)
        elif lower_url.endswith(".txt") and resolved_election_id in lower_url:
            details = _replace_results(conn, resolved_election_id, endpoint_url, payload)
        else:
            details = {"processed": 0}

        return {
            "ok": True,
            "processed": details.get("processed", 0),
            "error": None,
            "snapshot_path": snapshot_path,
            "started": started,
            "finished": utc_now_iso(),
            "election_id": resolved_election_id,
        }
    except Exception as exc:
        return {
            "ok": False,
            "processed": 0,
            "error": str(exc),
            "snapshot_path": snapshot_path,
            "started": started,
            "finished": utc_now_iso(),
            "election_id": resolved_election_id,
        }
