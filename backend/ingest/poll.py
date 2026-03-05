import hashlib
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from db import db_conn, init_db, upsert_endpoint, utc_now_iso
from ingest.discover import discover_for_election, endpoints_file_for, write_endpoints
from ingest.parse import parse_endpoint

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("poller")

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_ROOT = BASE_DIR / "data" / "elections"
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "45"))
POLL_STAGGER = float(os.getenv("POLL_STAGGER_SECONDS", "1"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
USER_AGENT = os.getenv("INGEST_USER_AGENT", "ElectionPoller/1.0")
ACTIVE_ELECTIONS_RAW = os.getenv("ACTIVE_ELECTIONS", "2082,2079")


def _active_elections() -> List[str]:
    elections = [item.strip() for item in ACTIVE_ELECTIONS_RAW.split(",") if item.strip()]
    return elections or ["2082", "2079"]


def _read_endpoints(election_id: str) -> List[str]:
    path = endpoints_file_for(election_id)
    if not path.exists():
        discovered = discover_for_election(["https://result.election.gov.np/"], election_id)
        write_endpoints(discovered, path)

    endpoints: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            value = line.strip()
            if value and not value.startswith("#"):
                endpoints.append(value)

    if not endpoints:
        discovered = discover_for_election(["https://result.election.gov.np/"], election_id)
        write_endpoints(discovered, path)
        endpoints = discovered

    return endpoints


def _safe_name(url: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", url)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:8]
    return "%s_%s" % (slug[:90], digest)


def _fetch(url: str) -> Tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        status = getattr(response, "status", response.getcode())
        data = response.read()
    return status, data


def _is_optional_lookup(url: str) -> bool:
    return "/Lookup/" in url


def _write_snapshot(raw_dir: Path, content: bytes, content_hash: str, ts: datetime) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    ts_name = ts.strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = raw_dir / (ts_name + ".json")
    snapshot_path.write_bytes(content)

    latest_tmp = raw_dir / "latest.json.tmp"
    latest_tmp.write_bytes(content)
    latest_tmp.replace(raw_dir / "latest.json")

    hash_tmp = raw_dir / "latest.sha256.tmp"
    hash_tmp.write_text(content_hash, encoding="utf-8")
    hash_tmp.replace(raw_dir / "latest.sha256")

    return snapshot_path


def _prune_snapshots(conn) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = conn.execute(
        "SELECT id, endpoint_id, ts, path FROM snapshots ORDER BY endpoint_id, ts ASC"
    ).fetchall()

    kept_buckets = set()
    to_delete: List[Tuple[int, str]] = []

    for row in rows:
        ts = row["ts"]
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            continue

        if dt >= cutoff:
            continue

        bucket = int(dt.timestamp() // 300)
        key = (row["endpoint_id"], bucket)
        if key in kept_buckets:
            to_delete.append((row["id"], row["path"]))
        else:
            kept_buckets.add(key)

    for snapshot_id, path in to_delete:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception:
            logger.warning("Failed deleting snapshot file: %s", path)
        conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))

    return len(to_delete)


def _process_endpoint(conn, election_id: str, url: str) -> Tuple[bool, bool, Optional[str]]:
    name = _safe_name(url)
    now_iso = utc_now_iso()

    upsert_endpoint(conn, election_id=election_id, url=url, name=name)
    row = conn.execute("SELECT id, last_hash FROM endpoints WHERE url = ?", (url,)).fetchone()
    endpoint_id = row["id"]
    last_hash = row["last_hash"]

    try:
        status_code, payload = _fetch(url)
    except HTTPError as exc:
        if exc.code == 404 and _is_optional_lookup(url):
            conn.execute(
                """
                UPDATE endpoints
                SET last_seen_ts = ?, last_status = ?, last_http_code = ?, last_error = ?
                WHERE id = ?
                """,
                (now_iso, "lookup_not_found_optional", exc.code, "HTTP %d" % exc.code, endpoint_id),
            )
            logger.warning("Optional lookup not found (HTTP 404): %s", url)
            return False, False, None

        conn.execute(
            """
            UPDATE endpoints
            SET last_seen_ts = ?, last_status = ?, last_http_code = ?, last_error = ?
            WHERE id = ?
            """,
            (now_iso, "fetch_http_error", exc.code, "HTTP %d" % exc.code, endpoint_id),
        )
        logger.error("Fetch failed for %s: HTTP %d", url, exc.code)
        return False, True, "HTTP %d" % exc.code
    except Exception as exc:
        error = str(exc)
        conn.execute(
            """
            UPDATE endpoints
            SET last_seen_ts = ?, last_status = ?, last_http_code = ?, last_error = ?
            WHERE id = ?
            """,
            (now_iso, "fetch_error", None, error, endpoint_id),
        )
        logger.error("Fetch failed for %s: %s", url, error)
        return False, True, error

    if status_code != 200:
        conn.execute(
            """
            UPDATE endpoints
            SET last_seen_ts = ?, last_status = ?, last_http_code = ?, last_error = ?
            WHERE id = ?
            """,
            (now_iso, "bad_status", status_code, "HTTP %d" % status_code, endpoint_id),
        )
        logger.warning("Non-200 for %s: %d", url, status_code)
        return False, True, "HTTP %d" % status_code

    content_hash = hashlib.sha256(payload).hexdigest()

    if last_hash and content_hash == last_hash:
        conn.execute(
            """
            UPDATE endpoints
            SET last_seen_ts = ?, last_status = ?, last_http_code = ?, last_error = NULL
            WHERE id = ?
            """,
            (now_iso, "ok_unchanged", status_code, endpoint_id),
        )
        logger.info("No change [%s]: %s (%d bytes)", election_id, url, len(payload))
        return False, False, None

    ts = datetime.now(timezone.utc)
    raw_dir = RAW_ROOT / election_id / "raw" / name
    snapshot_path = _write_snapshot(raw_dir, payload, content_hash, ts)

    conn.execute(
        """
        INSERT INTO snapshots(endpoint_id, election_id, ts, path, hash, size_bytes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (endpoint_id, election_id, utc_now_iso(), str(snapshot_path), content_hash, len(payload)),
    )

    parse_result = parse_endpoint(conn, url, payload, str(snapshot_path), election_id=election_id)
    parse_error = parse_result.get("error")

    conn.execute(
        """
        UPDATE endpoints
        SET
            election_id = ?,
            last_hash = ?,
            last_seen_ts = ?,
            last_changed_ts = ?,
            last_status = ?,
            last_http_code = ?,
            last_error = ?
        WHERE id = ?
        """,
        (
            election_id,
            content_hash,
            now_iso,
            now_iso,
            "ok_changed" if parse_result.get("ok") else "parse_error",
            status_code,
            parse_error,
            endpoint_id,
        ),
    )

    if parse_error:
        logger.error("Parse error [%s] for %s: %s", election_id, url, parse_error)
        return True, True, parse_error

    logger.info("Changed [%s]: %s (%d bytes)", election_id, url, len(payload))
    return True, False, None


def run_forever() -> None:
    init_db()

    for election_id in _active_elections():
        (RAW_ROOT / election_id / "raw").mkdir(parents=True, exist_ok=True)

    cycle_number = 0
    while True:
        started = datetime.now(timezone.utc)
        elections = _active_elections()

        with db_conn() as conn:
            total_changed = 0
            total_errors = 0

            for election_id in elections:
                started_iso = utc_now_iso()
                endpoints = _read_endpoints(election_id)
                logger.info("Starting poll cycle for election %s with %d endpoints", election_id, len(endpoints))

                changed = 0
                errors = 0
                run_id = conn.execute(
                    """
                    INSERT INTO ingest_runs(election_id, started_ts, changed_endpoints, errors, notes)
                    VALUES (?, ?, 0, 0, ?)
                    """,
                    (election_id, started_iso, "cycle=%d" % cycle_number),
                ).lastrowid

                for idx, endpoint in enumerate(endpoints):
                    did_change, had_error, _ = _process_endpoint(conn, election_id, endpoint)
                    changed += int(did_change)
                    errors += int(had_error)
                    conn.commit()

                    if idx < len(endpoints) - 1:
                        time.sleep(POLL_STAGGER)

                conn.execute(
                    """
                    UPDATE ingest_runs
                    SET finished_ts = ?, changed_endpoints = ?, errors = ?, notes = ?
                    WHERE id = ?
                    """,
                    (utc_now_iso(), changed, errors, "cycle=%d" % cycle_number, run_id),
                )
                conn.commit()

                total_changed += changed
                total_errors += errors

            pruned = _prune_snapshots(conn)
            conn.commit()

        cycle_number += 1
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        sleep_for = max(0, POLL_INTERVAL - elapsed)
        logger.info(
            "Poll cycle done: elections=%d changed=%d errors=%d pruned=%d elapsed=%.1fs sleep=%.1fs",
            len(elections),
            total_changed,
            total_errors,
            pruned,
            elapsed,
            sleep_for,
        )
        time.sleep(sleep_for)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
