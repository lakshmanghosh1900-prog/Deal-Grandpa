from deal_grandpa.domain.models import Outcome

QUERY = "I need a phone under 30000 with 8GB RAM 128GB storage"

def test_demo_recommends_only_one_exact_variant_with_full_gates(service):
    decision = service.run(QUERY)
    assert decision.outcome is Outcome.RECOMMEND
    assert decision.recommendation is not None
    assert len(decision.ranked) == 3
    assert len(decision.excluded) == 175
    assert len({item.offer.source.source_id for item in decision.ranked}) == len(decision.ranked)
    assert all(len(item.gates) == 7 for item in decision.ranked)
    assert all(g.status.value == "PASS" for item in decision.ranked for g in item.gates)
    assert all(number.source_ts and number.fetched_at and number.rules_hash == decision.rules_hash and number.run_id == decision.run_id for item in decision.ranked for number in item.evidence_numbers)
    assert all(any(number.name == "price_minor" for number in item.evidence_numbers) for item in decision.ranked + decision.excluded)

def test_golden_dataset_excludes_near_misses_stale_and_suspicious_prices(service):
    decision = service.run(QUERY)
    reasons = {gate.reason_code for item in decision.excluded for gate in item.gates}
    assert {"VARIANT_MISMATCH", "STALE_SOURCE_TIMESTAMP", "FUTURE_SOURCE_TIMESTAMP", "ANOMALY_MODIFIED_Z", "UNAPPROVED_DOMAIN", "REDIRECT_TRICK", "COUNTRY_RESTRICTED", "OUT_OF_STOCK", "PROMO_EXPIRED", "SHIPPING_UNKNOWN", "INVALID_GTIN", "SOURCE_BREAKER_OPEN"} <= reasons
    assert decision.recommendation.effective_cost.to_minor() <= decision.query.budget.to_minor()

def test_replay_matches_immutable_snapshot_and_audit_chain(service):
    decision = service.run(QUERY)
    replay = service.replay(decision.run_id)
    assert replay["status"] == "MATCH"
    ok, message = service.verify_log()
    assert ok, message

def test_timeout_once_uses_only_allowlisted_recovery(service):
    service.fault_mode = "timeout_once"
    decision = service.run(QUERY)
    assert decision.outcome is Outcome.RECOMMEND
    row = service.store.run(decision.run_id)
    assert 'retry' in row['result_json']

def test_persistent_connector_failure_abstains(service):
    service.fault_mode = "persistent_failure"
    decision = service.run(QUERY)
    assert decision.outcome is Outcome.ABSTAIN
    assert decision.recommendation is None
    assert any("retry" in reason and "quarantine" in reason for reason in decision.reasons)
