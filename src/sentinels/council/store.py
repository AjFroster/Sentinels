"""Durable storage for deliberations and the event log behind them.

Two tables. ``deliberations`` holds finished briefs; ``events`` holds every
stage transition that produced one, in order.

The event log is a hash chain: each row carries the digest of the row before
it. That makes the log tamper-evident without any key management -- you can
prove afterwards that a sealed question never had a cloud member in it, which
is the whole reason this project prefers local models. Signing with a real
keypair would prove *who* wrote the log; a chain only proves it has not been
edited since. That is the weaker claim, and it is the honest one to make here.

sqlite3 is synchronous, so every call hops to a worker thread. Volume is a
handful of rows per deliberation, so a connection per call is cheaper than
managing a shared one across an event loop.
"""

import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

GENESIS = "0" * 64

SCHEMA = """
CREATE TABLE IF NOT EXISTS deliberations (
    id             TEXT PRIMARY KEY,
    question       TEXT NOT NULL,
    classification TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    elapsed_s      REAL NOT NULL,
    chairman       TEXT NOT NULL,
    members        TEXT NOT NULL,   -- json array
    brief          TEXT NOT NULL,   -- json object
    egress         TEXT,
    egress_bytes   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    deliberation_id TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    event           TEXT NOT NULL,
    data            TEXT NOT NULL,  -- canonical json
    at              TEXT NOT NULL,
    prev_hash       TEXT NOT NULL,
    hash            TEXT NOT NULL,
    PRIMARY KEY (deliberation_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_delib ON events(deliberation_id, seq);
CREATE INDEX IF NOT EXISTS idx_delib_created ON deliberations(created_at DESC);
"""


def db_path() -> Path:
    """Where the database lives. Honours XDG, falls back to ~/.local/share."""
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "sentinels" / "sentinels.db"


def _canonical(data: Any) -> str:
    """Stable JSON so a hash computed today matches one recomputed later."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _digest(prev_hash: str, seq: int, event: str, data_json: str, at: str) -> str:
    payload = f"{prev_hash}|{seq}|{event}|{data_json}|{at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(path: Optional[Path] = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


class Store:
    """Everything the council needs to remember, on disk."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or db_path()
        # Appending reads the current max seq, then inserts seq+1. Stage 1 runs
        # every member concurrently, so without this two of them read the same
        # max and collide on the primary key. Cheap to hold: a handful of rows
        # per deliberation, and the chain has to be built in order anyway.
        self._append_lock = asyncio.Lock()

    # -- sync bodies, run off-loop by the async wrappers below ------------

    def _init(self) -> None:
        with _connect(self.path) as conn:
            conn.executescript(SCHEMA)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass  # not every filesystem supports it

    def _append(self, delib_id: str, event: str, data: dict) -> str:
        at = datetime.now(timezone.utc).isoformat()
        blob = _canonical(data)
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT seq, hash FROM events WHERE deliberation_id=? "
                "ORDER BY seq DESC LIMIT 1", (delib_id,)
            ).fetchone()
            seq = (row["seq"] + 1) if row else 0
            prev = row["hash"] if row else GENESIS
            digest = _digest(prev, seq, event, blob, at)
            conn.execute(
                "INSERT INTO events(deliberation_id,seq,event,data,at,prev_hash,hash)"
                " VALUES (?,?,?,?,?,?,?)",
                (delib_id, seq, event, blob, at, prev, digest),
            )
        return digest

    def _save(self, delib_id: str, record: dict) -> None:
        with _connect(self.path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO deliberations"
                "(id,question,classification,created_at,elapsed_s,chairman,"
                "members,brief,egress,egress_bytes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    delib_id,
                    record["question"],
                    record["classification"],
                    record["created_at"],
                    record["stages_elapsed_s"],
                    record["chairman"],
                    _canonical(record["members"]),
                    _canonical(record["brief"]),
                    record.get("egress"),
                    int(record.get("egress_bytes") or 0),
                ),
            )

    def _recent(self, limit: int) -> list[dict]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT id,question,classification,created_at,elapsed_s"
                " FROM deliberations ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": r["id"], "question": r["question"],
                "classification": r["classification"],
                "created_at": r["created_at"], "elapsed_s": r["elapsed_s"],
            }
            for r in rows
        ]

    def _get(self, delib_id: str) -> Optional[dict]:
        with _connect(self.path) as conn:
            r = conn.execute(
                "SELECT * FROM deliberations WHERE id=?", (delib_id,)
            ).fetchone()
        if r is None:
            return None
        return {
            "question": r["question"],
            "classification": r["classification"],
            "created_at": r["created_at"],
            "stages_elapsed_s": r["elapsed_s"],
            "chairman": r["chairman"],
            "members": json.loads(r["members"]),
            "brief": json.loads(r["brief"]),
            "egress": r["egress"],
        }

    def _egress_total(self) -> int:
        """Bytes that have ever crossed the boundary. Survives restart on purpose:
        a counter that resets to zero would make the titlebar a lie."""
        with _connect(self.path) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(egress_bytes),0) AS total FROM deliberations"
            ).fetchone()
        return int(row["total"])

    def _events(self, delib_id: str) -> list[dict]:
        with _connect(self.path) as conn:
            rows = conn.execute(
                "SELECT seq,event,data,at,prev_hash,hash FROM events"
                " WHERE deliberation_id=? ORDER BY seq", (delib_id,)
            ).fetchall()
        return [
            {"seq": r["seq"], "event": r["event"], "data": json.loads(r["data"]),
             "at": r["at"], "prev_hash": r["prev_hash"], "hash": r["hash"]}
            for r in rows
        ]

    def _verify(self, delib_id: str) -> dict:
        """Recompute the chain. Reports the first row that does not match."""
        rows = self._events(delib_id)
        if not rows:
            return {"ok": False, "checked": 0, "reason": "no events recorded"}
        prev = GENESIS
        for row in rows:
            expected = _digest(prev, row["seq"], row["event"],
                               _canonical(row["data"]), row["at"])
            if row["prev_hash"] != prev:
                return {"ok": False, "checked": row["seq"],
                        "reason": f"event {row['seq']} does not follow the previous hash"}
            if row["hash"] != expected:
                return {"ok": False, "checked": row["seq"],
                        "reason": f"event {row['seq']} has been altered since it was written"}
            prev = row["hash"]
        return {"ok": True, "checked": len(rows), "head": prev, "reason": None}

    # -- async surface ----------------------------------------------------

    async def init(self) -> None:
        await asyncio.to_thread(self._init)

    async def append(self, delib_id: str, event: str, data: dict) -> str:
        async with self._append_lock:
            return await asyncio.to_thread(self._append, delib_id, event, data)

    async def save(self, delib_id: str, record: dict) -> None:
        await asyncio.to_thread(self._save, delib_id, record)

    async def recent(self, limit: int = 100) -> list[dict]:
        return await asyncio.to_thread(self._recent, limit)

    async def get(self, delib_id: str) -> Optional[dict]:
        return await asyncio.to_thread(self._get, delib_id)

    async def egress_total(self) -> int:
        return await asyncio.to_thread(self._egress_total)

    async def events(self, delib_id: str) -> list[dict]:
        return await asyncio.to_thread(self._events, delib_id)

    async def verify(self, delib_id: str) -> dict:
        return await asyncio.to_thread(self._verify, delib_id)
