import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "data" / "elections" / "2082" / "state.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_db_path() -> Path:
    configured = os.getenv("DB_PATH")
    return Path(configured) if configured else DEFAULT_DB_PATH


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


@contextmanager
def db_conn() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_ddl: str) -> None:
    if not _has_column(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}")


def init_db() -> None:
    with db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id TEXT NOT NULL DEFAULT '2082',
                url TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                last_hash TEXT,
                last_seen_ts TEXT,
                last_changed_ts TEXT,
                last_status TEXT,
                last_http_code INTEGER,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint_id INTEGER NOT NULL,
                election_id TEXT NOT NULL DEFAULT '2082',
                ts TEXT NOT NULL,
                path TEXT NOT NULL,
                hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                FOREIGN KEY(endpoint_id) REFERENCES endpoints(id)
            );

            CREATE TABLE IF NOT EXISTS results_latest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id TEXT NOT NULL DEFAULT '2082',
                race_type TEXT,
                state_id TEXT,
                state_name TEXT,
                district_id TEXT,
                district_name TEXT,
                constituency_id TEXT,
                constituency_name TEXT,
                constituency_key TEXT,
                municipality TEXT,
                ward TEXT,
                candidate TEXT,
                party TEXT,
                party_symbol_id TEXT,
                party_symbol_name TEXT,
                votes INTEGER,
                status TEXT,
                rank INTEGER,
                current_address TEXT,
                source_endpoint TEXT NOT NULL,
                source_json TEXT,
                updated_ts TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lookups_state (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                extra_json TEXT
            );

            CREATE TABLE IF NOT EXISTS lookups_district (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                state_id TEXT,
                extra_json TEXT
            );

            CREATE TABLE IF NOT EXISTS party_agg_by_election (
                election_id TEXT NOT NULL,
                party TEXT NOT NULL,
                party_symbol_name TEXT,
                elected_count INTEGER NOT NULL,
                leading_count INTEGER NOT NULL,
                total_votes INTEGER NOT NULL,
                updated_ts TEXT NOT NULL,
                PRIMARY KEY (election_id, party)
            );

            CREATE TABLE IF NOT EXISTS constituency_winners (
                election_id TEXT NOT NULL,
                constituency_key TEXT NOT NULL,
                constituency_id TEXT,
                constituency_name TEXT,
                district_id TEXT,
                district_name TEXT,
                candidate TEXT,
                party TEXT,
                party_symbol_name TEXT,
                votes INTEGER,
                status TEXT,
                updated_ts TEXT NOT NULL,
                PRIMARY KEY (election_id, constituency_key)
            );

            CREATE TABLE IF NOT EXISTS ingest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                election_id TEXT,
                started_ts TEXT NOT NULL,
                finished_ts TEXT,
                changed_endpoints INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                notes TEXT
            );
            """
        )

        # Lightweight migrations for older local DB files.
        _ensure_column(conn, "endpoints", "election_id", "TEXT NOT NULL DEFAULT '2082'")
        _ensure_column(conn, "snapshots", "election_id", "TEXT NOT NULL DEFAULT '2082'")
        _ensure_column(conn, "results_latest", "constituency_key", "TEXT")
        _ensure_column(conn, "results_latest", "party_symbol_id", "TEXT")
        _ensure_column(conn, "results_latest", "party_symbol_name", "TEXT")
        _ensure_column(conn, "ingest_runs", "election_id", "TEXT")

        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_endpoints_election ON endpoints(election_id);
            CREATE INDEX IF NOT EXISTS idx_endpoints_url ON endpoints(url);
            CREATE INDEX IF NOT EXISTS idx_snapshots_endpoint_ts ON snapshots(endpoint_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_snapshots_election_ts ON snapshots(election_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_results_election ON results_latest(election_id);
            CREATE INDEX IF NOT EXISTS idx_results_state ON results_latest(state_id);
            CREATE INDEX IF NOT EXISTS idx_results_district ON results_latest(district_id);
            CREATE INDEX IF NOT EXISTS idx_results_constituency ON results_latest(constituency_id);
            CREATE INDEX IF NOT EXISTS idx_results_constituency_key ON results_latest(constituency_key);
            CREATE INDEX IF NOT EXISTS idx_results_party ON results_latest(party);
            CREATE INDEX IF NOT EXISTS idx_results_status ON results_latest(status);
            CREATE INDEX IF NOT EXISTS idx_results_votes ON results_latest(votes DESC);
            CREATE INDEX IF NOT EXISTS idx_runs_election_started ON ingest_runs(election_id, started_ts DESC);
            """
        )

        conn.commit()


def upsert_endpoint(
    conn: sqlite3.Connection,
    *,
    election_id: str,
    url: str,
    name: str,
    last_seen_ts: Optional[str] = None,
    last_status: Optional[str] = None,
    last_http_code: Optional[int] = None,
    last_error: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO endpoints(election_id, url, name, last_seen_ts, last_status, last_http_code, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            election_id = excluded.election_id,
            name = excluded.name,
            last_seen_ts = COALESCE(excluded.last_seen_ts, endpoints.last_seen_ts),
            last_status = COALESCE(excluded.last_status, endpoints.last_status),
            last_http_code = COALESCE(excluded.last_http_code, endpoints.last_http_code),
            last_error = excluded.last_error
        """,
        (election_id, url, name, last_seen_ts, last_status, last_http_code, last_error),
    )
