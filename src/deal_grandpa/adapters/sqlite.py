from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from deal_grandpa.adapters.checkpoint import AuditCheckpointError, FileAuditCheckpoint
from deal_grandpa.domain.canonical import canonical_hash


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS source_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  fixture_id TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  source_registry_json TEXT NOT NULL,
  redacted_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL REFERENCES source_snapshots(snapshot_id),
  query_text TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  rules_hash TEXT NOT NULL,
  clock TEXT NOT NULL,
  source_registry_json TEXT NOT NULL,
  review_decisions_json TEXT NOT NULL,
  state TEXT NOT NULL,
  status TEXT NOT NULL,
  review_version TEXT NOT NULL,
  result_json TEXT,
  result_hash TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL UNIQUE,
  run_id TEXT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
  source_id TEXT PRIMARY KEY,
  breaker_state TEXT NOT NULL,
  dead_letter_count INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SQLiteStore:
    def __init__(self, path: str | Path, checkpoint_path: str | Path | None = None, chain_id: str = "deal-grandpa-fixture-chain-v1"):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(_SCHEMA)
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(runs)").fetchall()}
        if "review_version" not in columns:
            self.connection.execute("ALTER TABLE runs ADD COLUMN review_version TEXT NOT NULL DEFAULT 'legacy-unknown'")
        self.connection.commit()
        self.checkpoint = FileAuditCheckpoint(
            checkpoint_path or Path(self.path).with_name("audit_checkpoint.json"),
            chain_id=chain_id,
        )

    def close(self) -> None:
        self.connection.close()

    def save_snapshot(self, snapshot_id: str, fixture_id: str, payload: dict[str, Any], registry: list[dict[str, Any]], captured_at: str) -> None:
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        registry_json = json.dumps(registry, ensure_ascii=False, separators=(",", ":"))
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO source_snapshots(snapshot_id,fixture_id,captured_at,payload_json,payload_hash,source_registry_json) VALUES (?,?,?,?,?,?)",
                (snapshot_id, fixture_id, captured_at, payload_json, canonical_hash(payload), registry_json),
            )

    def snapshot(self, snapshot_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM source_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()

    def start_run(self, run_id: str, snapshot_id: str, query_text: str, policy_version: str, rules_hash: str, review_version: str, clock: str, registry: list[dict[str, Any]], review_decisions: list[dict[str, Any]]) -> None:
        stamp = now_iso()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO runs(run_id,snapshot_id,query_text,policy_version,rules_hash,review_version,clock,source_registry_json,review_decisions_json,state,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, snapshot_id, query_text, policy_version, rules_hash, review_version, clock, json.dumps(registry, separators=(",", ":")), json.dumps(review_decisions, separators=(",", ":")), "SNAPSHOT", "RUNNING", stamp, stamp),
            )

    def update_state(self, run_id: str, state: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE runs SET state=?,updated_at=? WHERE run_id=?", (state, now_iso(), run_id))

    def finish_run(self, run_id: str, status: str, result: dict[str, Any], result_hash: str) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET state=?,status=?,result_json=?,result_hash=?,updated_at=? WHERE run_id=?",
                ("COMPLETE", status, json.dumps(result, ensure_ascii=False, separators=(",", ":")), result_hash, now_iso(), run_id),
            )

    def run(self, run_id: str) -> sqlite3.Row | None:
        return self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()

    def append_audit(self, event_id: str, run_id: str | None, event_type: str, actor: str, payload: dict[str, Any], created_at: str | None = None) -> str:
        current_rows = self.connection.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence").fetchall()
        if current_rows:
            ok, message = self.checkpoint.verify(len(current_rows), current_rows[-1]["sequence"], current_rows[-1]["event_hash"])
            if not ok and message != "AUDIT_CHECKPOINT_MISSING":
                raise AuditCheckpointError(message)
        previous = self.connection.execute("SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else "GENESIS"
        event_payload = {"event_type": event_type, "actor": actor, "run_id": run_id, "payload": payload, "previous_hash": previous_hash}
        event_hash = canonical_hash(event_payload)
        with self.connection:
            self.connection.execute(
                "INSERT INTO audit_events(event_id,run_id,event_type,actor,payload_json,previous_hash,event_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (event_id, run_id, event_type, actor, json.dumps(payload, ensure_ascii=False, separators=(",", ":")), previous_hash, event_hash, created_at or now_iso()),
            )
        count = self.connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
        head = self.connection.execute("SELECT sequence,event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1").fetchone()
        self.checkpoint.write(int(count), int(head["sequence"]), str(head["event_hash"]))
        return event_hash

    def verify_audit(self) -> tuple[bool, str]:
        previous = "GENESIS"
        rows = self.connection.execute("SELECT * FROM audit_events ORDER BY sequence").fetchall()
        for expected_sequence, row in enumerate(rows, start=1):
            if row["sequence"] != expected_sequence:
                return False, f"audit sequence gap at sequence {row['sequence']}"
            payload = json.loads(row["payload_json"])
            expected = canonical_hash({"event_type": row["event_type"], "actor": row["actor"], "run_id": row["run_id"], "payload": payload, "previous_hash": previous})
            if row["previous_hash"] != previous or row["event_hash"] != expected:
                return False, f"audit chain mismatch at sequence {row['sequence']}"
            previous = row["event_hash"]
        if not rows:
            return True, "verified 0 events"
        checkpoint_ok, checkpoint_message = self.checkpoint.verify(len(rows), rows[-1]["sequence"], rows[-1]["event_hash"])
        if not checkpoint_ok:
            return False, checkpoint_message
        return True, f"verified {len(rows)} events ({checkpoint_message})"

    def upsert_source_health(self, source_id: str, breaker_state: str, dead_letter_count: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO source_health(source_id,breaker_state,dead_letter_count,updated_at) VALUES (?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET breaker_state=excluded.breaker_state,dead_letter_count=excluded.dead_letter_count,updated_at=excluded.updated_at",
                (source_id, breaker_state, dead_letter_count, now_iso()),
            )

    def source_health(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT source_id,breaker_state,dead_letter_count,updated_at FROM source_health ORDER BY source_id").fetchall()
        return [dict(row) for row in rows]

    def purge_expired_raw(self, now: datetime | None = None) -> int:
        """Daily deletion workflow: redact raw payloads while preserving hashes and decisions."""
        current = now or datetime.now(timezone.utc)
        rows = self.connection.execute("SELECT snapshot_id,captured_at,source_registry_json FROM source_snapshots WHERE redacted_at IS NULL").fetchall()
        removed = 0
        with self.connection:
            for row in rows:
                captured = datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00"))
                registry = json.loads(row["source_registry_json"])
                retention = max((int(item.get("retention_days", 1)) for item in registry), default=1)
                if (current - captured).total_seconds() >= retention * 86400:
                    self.connection.execute("UPDATE source_snapshots SET payload_json='[REDACTED]',redacted_at=? WHERE snapshot_id=?", (current.isoformat().replace("+00:00", "Z"), row["snapshot_id"]))
                    removed += 1
        return removed
