import json
import sqlite3
from pathlib import Path

from deal_grandpa.adapters.sqlite import SQLiteStore


def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "audit.sqlite3")


def _events(store: SQLiteStore):
    for number in range(3):
        store.append_audit(f"event-{number}", None, "TEST", "test", {"number": number})


def test_audit_tail_deletion_detected(tmp_path):
    store = _store(tmp_path)
    _events(store)
    store.connection.execute("DELETE FROM audit_events WHERE sequence=(SELECT MAX(sequence) FROM audit_events)")
    store.connection.commit()
    assert store.verify_audit() == (False, "TAIL_DELETED")


def test_audit_head_hash_mismatch(tmp_path):
    store = _store(tmp_path)
    _events(store)
    checkpoint = json.loads(store.checkpoint.path.read_text(encoding="utf-8"))
    checkpoint["head_hash"] = "tampered"
    store.checkpoint.path.write_text(json.dumps(checkpoint), encoding="utf-8")
    assert store.verify_audit() == (False, "AUDIT_HEAD_MISMATCH")


def test_audit_changed_deleted_middle_and_reordered_events_detected(tmp_path):
    store = _store(tmp_path)
    _events(store)
    store.connection.execute("UPDATE audit_events SET payload_json='{}' WHERE sequence=2")
    store.connection.commit()
    assert store.verify_audit()[0] is False

    store.connection.execute("UPDATE audit_events SET payload_json=? WHERE sequence=2", ('{"number":1}',))
    store.connection.execute("DELETE FROM audit_events WHERE sequence=2")
    store.connection.commit()
    assert store.verify_audit()[0] is False

    store.connection.execute("UPDATE audit_events SET sequence=-sequence")
    store.connection.commit()
    assert store.verify_audit()[0] is False


def test_replay_from_stored_snapshot_identical_and_survives_missing_fixture(tmp_path):
    from datetime import datetime, timezone
    from deal_grandpa.orchestrator import DealGrandpaService

    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    store = SQLiteStore(tmp_path / "replay.sqlite3")
    service = DealGrandpaService(store, "tests/fixtures/golden_offers.json", clock=lambda: now)
    decision = service.run("I need a phone under 30000 with 8GB RAM 128GB storage", now=now)
    replay_service = DealGrandpaService(store, str(tmp_path / "does-not-exist.json"), clock=lambda: now)
    assert replay_service.replay(decision.run_id)["status"] == "MATCH"


def test_replay_fails_on_policy_version_change(tmp_path):
    from datetime import datetime, timezone
    from deal_grandpa.orchestrator import DealGrandpaService

    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    store = SQLiteStore(tmp_path / "policy.sqlite3")
    service = DealGrandpaService(store, "tests/fixtures/golden_offers.json", clock=lambda: now)
    decision = service.run("I need a phone under 30000 with 8GB RAM 128GB storage", now=now)
    store.connection.execute("UPDATE runs SET policy_version='changed-policy' WHERE run_id=?", (decision.run_id,))
    store.connection.commit()
    assert service.replay(decision.run_id)["reason"] == "POLICY_VERSION_MISMATCH"


def test_replay_fails_on_snapshot_hash_tampering(tmp_path):
    from datetime import datetime, timezone
    from deal_grandpa.orchestrator import DealGrandpaService

    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    store = SQLiteStore(tmp_path / "snapshot.sqlite3")
    service = DealGrandpaService(store, "tests/fixtures/golden_offers.json", clock=lambda: now)
    decision = service.run("I need a phone under 30000 with 8GB RAM 128GB storage", now=now)
    store.connection.execute("UPDATE source_snapshots SET payload_json='{}' WHERE snapshot_id=(SELECT snapshot_id FROM runs WHERE run_id=?)", (decision.run_id,))
    store.connection.commit()
    assert service.replay(decision.run_id)["reason"] == "SNAPSHOT_HASH_MISMATCH"


def test_prior_data_preserved_after_connector_failure(tmp_path):
    from datetime import datetime, timezone
    import json
    from deal_grandpa.orchestrator import DealGrandpaService

    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    store = SQLiteStore(tmp_path / "recovery.sqlite3")
    service = DealGrandpaService(store, "tests/fixtures/golden_offers.json", clock=lambda: now)
    decision = service.run("I need a phone under 30000 with 8GB RAM 128GB storage", now=now)
    before = json.loads(store.run(decision.run_id)["result_json"])
    service.fault_mode = "persistent_failure"
    failed = service.run("I need a phone under 30000 with 8GB RAM 128GB storage", now=now)
    after = json.loads(store.run(decision.run_id)["result_json"])
    assert failed.outcome.value == "ABSTAIN"
    assert len(before["ranked"]) == len(after["ranked"]) == 3
