from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class AuditCheckpointError(RuntimeError):
    """The local audit chain cannot safely advance or be verified."""


@dataclass(frozen=True)
class AuditCheckpoint:
    chain_id: str
    count: int
    head_sequence: int
    head_hash: str


class FileAuditCheckpoint:
    """Small external checkpoint anchor kept outside the SQLite database."""

    schema_version = 1

    def __init__(self, path: str | Path, chain_id: str = "deal-grandpa-fixture-chain-v1"):
        self.path = Path(path)
        self.chain_id = chain_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self) -> AuditCheckpoint | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != self.schema_version or payload.get("chain_id") != self.chain_id:
                raise AuditCheckpointError("AUDIT_CHECKPOINT_MISMATCH")
            return AuditCheckpoint(
                chain_id=payload["chain_id"],
                count=int(payload["count"]),
                head_sequence=int(payload["head_sequence"]),
                head_hash=str(payload["head_hash"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuditCheckpointError("AUDIT_CHECKPOINT_INVALID") from error

    def write(self, count: int, head_sequence: int, head_hash: str) -> None:
        previous = self.read()
        if previous is not None and (count < previous.count or (count == previous.count and head_hash != previous.head_hash)):
            raise AuditCheckpointError("AUDIT_HEAD_MISMATCH")
        payload = {
            "schema_version": self.schema_version,
            "chain_id": self.chain_id,
            "count": count,
            "head_sequence": head_sequence,
            "head_hash": head_hash,
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)

    def verify(self, count: int, head_sequence: int, head_hash: str) -> tuple[bool, str]:
        checkpoint = self.read()
        if checkpoint is None:
            return False, "AUDIT_CHECKPOINT_MISSING"
        if checkpoint.count != count or checkpoint.head_sequence != head_sequence or checkpoint.head_hash != head_hash:
            if count < checkpoint.count or head_sequence < checkpoint.head_sequence:
                return False, "TAIL_DELETED"
            return False, "AUDIT_HEAD_MISMATCH"
        return True, "AUDIT_CHECKPOINT_MATCH"
