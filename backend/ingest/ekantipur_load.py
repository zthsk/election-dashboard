import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from db import BASE_DIR, db_conn, init_db, utc_now_iso
from ingest.parse import _recompute_constituency_winners, _recompute_party_agg

EKANTIPUR_DIR = BASE_DIR / "data" / "elections" / "ekantipur_2082"
PARTIES_JSONL = EKANTIPUR_DIR / "parties.jsonl"
CANDIDATES_JSONL = EKANTIPUR_DIR / "candidates_flat.jsonl"
PROFILES_JSONL = EKANTIPUR_DIR / "profiles.jsonl"

ELECTION_ID_DEFAULT = "2082"
SOURCE_ENDPOINT = "ekantipur_2082"


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _slug_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    slug = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return slug or None


def _parse_constituency(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Ekantipur constituency strings look like 'Jhapa- 1', 'Panchthar-1', etc.
    Return (constituency_name, constituency_id_str).
    """
    if not raw:
        return None, None
    name = str(raw).strip()
    m = re.search(r"(\d+)\s*$", name)
    if not m:
        return name, None
    return name, m.group(1)


def _load_party_map() -> Dict[int, Dict[str, Any]]:
    parties = _load_jsonl(PARTIES_JSONL)
    mapping: Dict[int, Dict[str, Any]] = {}
    for row in parties:
        pid = row.get("party_id")
        if not isinstance(pid, int):
            continue
        mapping[pid] = {
            "name_eng": row.get("name_eng") or row.get("name_nep"),
            "name_nep": row.get("name_nep") or row.get("name_eng"),
            "symbol_url": row.get("symbol_url"),
        }
    return mapping


def _load_profile_index() -> Dict[int, Dict[str, Any]]:
    """
    Build a lightweight index of profiles keyed by candidate_profile_id.
    We keep only a few frequently-used fields to avoid bloating source_json too much.
    """
    profiles = _load_jsonl(PROFILES_JSONL)
    index: Dict[int, Dict[str, Any]] = {}
    for row in profiles:
        pid = row.get("candidate_profile_id")
        if not isinstance(pid, int):
            continue
        eng = row.get("eng") or {}
        nep = row.get("nep") or {}
        eng_info = eng.get("info") or {}
        nep_info = nep.get("info") or {}
        index[pid] = {
            "candidate_profile_id": pid,
            "photo_path": row.get("photo_saved"),
            "eng": {
                "candidate_name": eng.get("candidate_name"),
                "info": {
                    "Electoral Constituency": eng_info.get("Electoral Constituency"),
                    "Political Party": eng_info.get("Political Party"),
                    "Birth Date": eng_info.get("Birth Date"),
                    "Birth Place": eng_info.get("Birth Place") or eng_info.get("Birth Place".lower()),
                    "Address": eng_info.get("Address"),
                    "Education": eng_info.get("Education"),
                    "Election System": eng_info.get("Election System"),
                },
            },
            "nep": {
                "candidate_name": nep.get("candidate_name"),
                "info": nep_info,
            },
        }
    return index


def _build_candidate_index(candidates: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
    """Index candidates_flat rows by candidate_profile_id for profile enrichment."""
    idx: Dict[int, Dict[str, Any]] = {}
    for row in candidates:
        pid = row.get("candidate_profile_id")
        if isinstance(pid, int):
            idx[pid] = row
    return idx


def _upsert_profiles(
    conn: Any,
    profiles_raw: List[Dict[str, Any]],
    parties: Dict[int, Dict[str, Any]],
    candidate_index: Dict[int, Dict[str, Any]],
    now_ts: str,
) -> int:
    """Populate candidates_profiles_2082 from raw profiles list, enriched with candidates_flat data."""
    conn.execute("DELETE FROM candidates_profiles_2082")
    upserted = 0
    for row in profiles_raw:
        profile_id = row.get("candidate_profile_id")
        if not isinstance(profile_id, int):
            continue

        eng = row.get("eng") or {}
        nep = row.get("nep") or {}
        eng_info = eng.get("info") or {}
        eng_sections = eng.get("sections") or {}
        nep_sections = nep.get("sections") or {}

        candidate_name = eng.get("candidate_name") or ""
        candidate_name_nep = nep.get("candidate_name") or ""

        # Cross-reference candidates_flat for party_id, constituency, district, province.
        cand_row = candidate_index.get(profile_id, {})
        party_id = cand_row.get("party_id")
        constituency_raw = cand_row.get("constituency")
        constituency_name, _ = _parse_constituency(constituency_raw)
        district = cand_row.get("district") or eng_info.get("District") or None
        province = cand_row.get("province") or eng_info.get("Province") or None

        party_info = parties.get(party_id or -1, {})
        party_name = party_info.get("name_eng") or party_info.get("name_nep")
        party_name_nep = party_info.get("name_nep") or party_info.get("name_eng")
        party_symbol_url = party_info.get("symbol_url")

        conn.execute(
            """
            INSERT OR REPLACE INTO candidates_profiles_2082(
                profile_id,
                candidate_name,
                candidate_name_nep,
                party_id,
                party_name,
                party_name_nep,
                party_symbol_url,
                constituency,
                district,
                province,
                birth_date,
                birth_place,
                address,
                education,
                election_system,
                photo_path,
                profile_url,
                political_history,
                parliament_tour,
                extra_json,
                updated_ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                candidate_name,
                candidate_name_nep,
                party_id,
                party_name,
                party_name_nep,
                party_symbol_url,
                constituency_name or eng_info.get("Electoral Constituency"),
                district,
                province,
                eng_info.get("Birth Date"),
                eng_info.get("Birth Place"),
                eng_info.get("Address"),
                eng_info.get("Education"),
                eng_info.get("Election System"),
                row.get("photo_saved"),
                row.get("profile_url"),
                json.dumps(
                    eng_sections.get("politicalHistory") or nep_sections.get("politicalHistory") or [],
                    ensure_ascii=False,
                ),
                json.dumps(
                    eng_sections.get("parliamentTour") or nep_sections.get("parliamentTour") or [],
                    ensure_ascii=False,
                ),
                json.dumps({"eng": eng, "nep": nep}, ensure_ascii=False),
                now_ts,
            ),
        )
        upserted += 1
    return upserted


def load_ekantipur_2082(election_id: str = ELECTION_ID_DEFAULT) -> Dict[str, Any]:
    """
    Load Ekantipur 2082 candidates into results_latest for the given election_id.

    - Pre-fills candidate/party/constituency rows with votes/status/rank empty.
    - Uses name-based mapping to lookups_state/lookups_district when available,
      falling back to synthetic IDs when necessary.
    - Also populates candidates_profiles_2082 with rich profile data.
    """
    init_db()

    parties = _load_party_map()
    profiles_raw = _load_jsonl(PROFILES_JSONL)
    profiles = _load_profile_index()
    candidates = _load_jsonl(CANDIDATES_JSONL)

    now_ts = utc_now_iso()
    inserted = 0

    with db_conn() as conn:
        # Populate rich profile table first, enriched with candidates_flat data.
        candidate_index = _build_candidate_index(candidates)
        profiles_upserted = _upsert_profiles(conn, profiles_raw, parties, candidate_index, now_ts)

        # Build lookup maps for existing states/districts.
        state_by_name: Dict[str, str] = {}
        for row in conn.execute("SELECT id, name FROM lookups_state").fetchall():
            key = (row["name"] or "").strip().lower()
            if key:
                state_by_name[key] = row["id"]

        district_by_state_and_name: Dict[Tuple[Optional[str], str], str] = {}
        for row in conn.execute("SELECT id, name, state_id FROM lookups_district").fetchall():
            key = ((row["state_id"] or "").strip() or None, (row["name"] or "").strip().lower())
            if key[1]:
                district_by_state_and_name[key] = row["id"]

        # Remove previous Ekantipur-only rows for this election so the importer is idempotent.
        conn.execute(
            "DELETE FROM results_latest WHERE election_id = ? AND source_endpoint = ?",
            (election_id, SOURCE_ENDPOINT),
        )

        for row in candidates:
            province = (row.get("province") or "").strip()
            district_name = (row.get("district") or "").strip()
            candidate_name = (row.get("candidate_name") or "").strip()
            constituency_raw = row.get("constituency")

            if not candidate_name or not district_name:
                continue

            constituency_name, constituency_id = _parse_constituency(constituency_raw)

            # Resolve state_id from lookups_state or fall back to a slug.
            state_id: Optional[str] = None
            state_name: Optional[str] = None
            if province:
                key = province.strip().lower()
                state_id = state_by_name.get(key)
                state_name = province
                if not state_id:
                    # Synthetic ID; also upsert into lookups_state so lookups APIs work.
                    state_id = _slug_id(province)
                    if state_id:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO lookups_state(id, name, extra_json)
                            VALUES (?, ?, NULL)
                            """,
                            (state_id, province),
                        )
                        state_by_name[key] = state_id

            # Resolve district_id similarly, using (state_id, district_name) where possible.
            district_key = (state_id, district_name.strip().lower())
            district_id: Optional[str] = district_by_state_and_name.get(district_key)
            if not district_id and district_name:
                district_id = _slug_id(district_name)
                if district_id:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO lookups_district(id, name, state_id, extra_json)
                        VALUES (?, ?, ?, NULL)
                        """,
                        (district_id, district_name, state_id),
                    )
                    district_by_state_and_name[district_key] = district_id

            # Constituency key aligned with geojson CON_KEY (DISTRICT_UPPER:NUM).
            constituency_key: Optional[str] = None
            if district_name and constituency_id:
                constituency_key = f"{district_name.upper()}:{constituency_id}"

            party_id = row.get("party_id")
            party_info = parties.get(party_id or -1, {})
            party_name = party_info.get("name_eng") or party_info.get("name_nep")
            party_symbol_name = party_info.get("name_eng") or party_info.get("name_nep")

            profile_id = row.get("candidate_profile_id")
            profile_extra = profiles.get(profile_id) if isinstance(profile_id, int) else None

            merged_source = {
                "ekantipur": row,
                "profile": profile_extra,
            }

            conn.execute(
                """
                INSERT INTO results_latest(
                    election_id,
                    race_type,
                    state_id,
                    state_name,
                    district_id,
                    district_name,
                    constituency_id,
                    constituency_name,
                    constituency_key,
                    municipality,
                    ward,
                    candidate,
                    party,
                    party_symbol_id,
                    party_symbol_name,
                    votes,
                    status,
                    rank,
                    current_address,
                    source_endpoint,
                    source_json,
                    updated_ts
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                    ?, ?, NULL, ?, NULL, NULL, NULL, NULL, ?, ?, ?
                )
                """,
                (
                    election_id,
                    "FPTP",
                    state_id,
                    state_name or province or None,
                    district_id,
                    district_name or None,
                    constituency_id,
                    constituency_name,
                    constituency_key,
                    candidate_name,
                    party_name,
                    party_symbol_name,
                    SOURCE_ENDPOINT,
                    json.dumps(merged_source, ensure_ascii=False),
                    now_ts,
                ),
            )
            inserted += 1

        # Recompute aggregates for this election_id so APIs see the new data.
        _recompute_party_agg(conn, election_id)
        _recompute_constituency_winners(conn, election_id)
        conn.commit()

    return {
        "election_id": election_id,
        "inserted": inserted,
        "profiles_upserted": profiles_upserted,
        "candidates_rows": len(candidates),
        "parties": len(parties),
        "profiles_indexed": len(profiles),
    }


def main() -> None:
    summary = load_ekantipur_2082()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

