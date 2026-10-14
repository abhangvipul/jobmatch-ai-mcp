"""Small SQLite ledger for idempotent application attempts and audit history."""
from __future__ import annotations
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> str:
    return os.getenv("JOBMATCH_DB_PATH", "jobmatch_applications.sqlite3")


def _connect():
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_url TEXT NOT NULL,
            profile_fingerprint TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            match_score REAL NOT NULL,
            status TEXT NOT NULL,
            provider_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(job_url, profile_fingerprint)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS application_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idempotency_key TEXT NOT NULL,
            event TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        )""")


def profile_fingerprint(profile: dict) -> str:
    # Fingerprint only selected normalized fields; do not store raw resume text.
    payload = {
        "skills": sorted(str(x).strip().lower() for x in profile.get("skills", []) if str(x).strip()),
        "years_experience": profile.get("years_experience", 0),
        "target_roles": sorted(str(x).strip().lower() for x in profile.get("target_roles", []) if str(x).strip()),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def make_idempotency_key(job_url: str, fingerprint: str) -> str:
    return hashlib.sha256(f"{job_url.strip()}::{fingerprint}".encode()).hexdigest()


def has_attempt(job_url: str, fingerprint: str) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM applications WHERE job_url=? AND profile_fingerprint=?", (job_url.strip(), fingerprint)).fetchone()
        return row is not None


def record_attempt(job_url: str, fingerprint: str, key: str, score: float, status: str, message: str = "") -> bool:
    """Insert one attempt. Returns False if it already exists (duplicate prevention)."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        with _connect() as conn:
            conn.execute("INSERT INTO applications(job_url, profile_fingerprint, idempotency_key, match_score, status, provider_message, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                         (job_url.strip(), fingerprint, key, float(score), status, message[:1000], now, now))
            conn.execute("INSERT INTO application_events(idempotency_key,event,details,created_at) VALUES(?,?,?,?)",
                         (key, status, json.dumps({"score": score, "message": message[:500]}), now))
        return True
    except sqlite3.IntegrityError:
        return False


def update_status(key: str, status: str, message: str = "") -> None:
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("UPDATE applications SET status=?, provider_message=?, updated_at=? WHERE idempotency_key=?",
                     (status, message[:1000], now, key))
        conn.execute("INSERT INTO application_events(idempotency_key,event,details,created_at) VALUES(?,?,?,?)",
                     (key, status, json.dumps({"message": message[:500]}), now))


def recent_applications(limit: int = 100) -> list[dict]:
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT job_url, match_score, status, provider_message, created_at, updated_at FROM applications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
