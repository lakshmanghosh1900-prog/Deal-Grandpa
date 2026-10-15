import json
from datetime import datetime, timezone
from pathlib import Path

from deal_grandpa.adapters.fixture_source import FixtureSource
from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.domain.models import Outcome
from deal_grandpa.domain.policy import validate_https_url
from deal_grandpa.orchestrator import DealGrandpaService


QUERY = "I need a phone under 30000 with 8GB RAM 128GB storage"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def _fixture(tmp_path: Path, mutate):
    payload = json.loads(Path("tests/fixtures/golden_offers.json").read_text(encoding="utf-8"))
    mutate(payload)
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(tmp_path: Path, mutate=lambda payload: None, query=QUERY):
    path = _fixture(tmp_path, mutate)
    store = SQLiteStore(tmp_path / "db.sqlite3")
    service = DealGrandpaService(store, str(path), clock=lambda: NOW)
    return service, service.run(query, now=NOW, actor="test")


def _offer(decision, offer_id):
    return next(item for item in decision.ranked + decision.excluded if item.offer.offer_id == offer_id)


def test_permission_missing_field_excluded(tmp_path):
    def mutate(payload):
        source = next(item for item in payload["sources"] if item["source_id"] == "shop-a")
        source["field_permissions"].remove("price_minor")
        source["field_permissions"].remove("merchant")
        next(item for item in payload["offers"] if item["offer_id"] == "offer-0001")["merchant"] = "PERMISSION_SENTINEL"

    service, decision = _run(tmp_path, mutate)
    item = _offer(decision, "offer-0001")
    assert item.gates[0].reason_code == "PERMISSION_DENIED"
    snapshot = service.store.snapshot(service.store.run(decision.run_id)["snapshot_id"])
    assert "PERMISSION_SENTINEL" not in snapshot["payload_json"]


def test_permission_revoked_excluded(tmp_path):
    def mutate(payload):
        source = next(item for item in payload["sources"] if item["source_id"] == "shop-a")
        source["field_permissions"].remove("price_minor")
        source["revoked_fields"] = ["price_minor"]

    _, decision = _run(tmp_path, mutate)
    assert _offer(decision, "offer-0001").gates[0].reason_code == "PERMISSION_REVOKED"


def test_condition_lane_separation_new_vs_refurbished(tmp_path):
    service, decision = _run(tmp_path)
    refurbished = _offer(decision, "offer-0020")
    open_box = _offer(decision, "offer-0021")
    assert refurbished.gates[0].reason_code == "CONDITION_LANE_MISMATCH"
    assert open_box.gates[0].reason_code == "CONDITION_LANE_MISMATCH"
    assert all(item.offer.condition == "new" for item in decision.ranked)
    loaded = FixtureSource(str(Path("tests/fixtures/golden_offers.json"))).load()
    deduplicated = service._deduplicate(loaded.offers)
    assert {item.offer_id for item in deduplicated} >= {"offer-0001", "offer-0020", "offer-0021"}


def test_malformed_url_rejected(tmp_path):
    _, decision = _run(tmp_path, lambda payload: next(item for item in payload["offers"] if item["offer_id"] == "offer-0001").update(url="not-a-url"))
    assert _offer(decision, "offer-0001").gates[-1].reason_code == "INVALID_MERCHANT_URL"


def test_url_domain_mismatch(tmp_path):
    _, decision = _run(tmp_path, lambda payload: next(item for item in payload["offers"] if item["offer_id"] == "offer-0001").update(url="https://shop-a.example.attacker/offers/1"))
    assert _offer(decision, "offer-0001").gates[-1].reason_code == "URL_DOMAIN_MISMATCH"


def test_url_redirect_off_allowlist(tmp_path):
    _, decision = _run(tmp_path, lambda payload: next(item for item in payload["offers"] if item["offer_id"] == "offer-0001").update(redirect_url="https://attacker.example/pay"))
    assert _offer(decision, "offer-0001").gates[-1].reason_code == "REDIRECT_TRICK"


def test_url_with_userinfo_rejected(tmp_path):
    _, decision = _run(tmp_path, lambda payload: next(item for item in payload["offers"] if item["offer_id"] == "offer-0001").update(url="https://user:pass@shop-a.example/offers/1"))
    assert _offer(decision, "offer-0001").gates[-1].reason_code == "INVALID_MERCHANT_URL"


def test_https_url_validation_is_exact_and_offline():
    assert validate_https_url("https://shop-a.example/offers/1", "shop-a.example")[0]
    assert validate_https_url("https://shop-a.example:443/offers/1", "shop-a.example")[0]
    assert not validate_https_url("http://shop-a.example/offers/1", "shop-a.example")[0]
    assert not validate_https_url("https://mirror.shop-a.example/offers/1", "shop-a.example")[0]


def test_active_genuine_discount_is_not_counted_twice(tmp_path):
    def mutate(payload):
        offer = next(item for item in payload["offers"] if item["offer_id"] == "offer-0001")
        offer.update(
            price_minor=2499900,
            list_price_minor=2699900,
            verified_discount_minor=200000,
            promo_starts_at="2026-10-16T09:00:00Z",
            promo_valid_until="2026-10-17T09:00:00Z",
        )

    _, decision = _run(tmp_path, mutate)
    item = _offer(decision, "offer-0001")
    assert item.effective_cost.to_minor() == 2499900
    assert item.offer.verified_discount.to_minor() == 200000


def test_currency_mismatch_reason(tmp_path):
    _, decision = _run(tmp_path)
    item = _offer(decision, "offer-0019")
    assert any(g.reason_code == "CURRENCY_MISMATCH" for g in item.gates)
    assert item.effective_cost is None


def test_no_suitable_offer_abstains(tmp_path):
    _, decision = _run(tmp_path, query="I need a phone under 100 with 64GB RAM 1GB storage")
    assert decision.outcome is Outcome.ABSTAIN
    assert decision.recommendation is None
    assert not decision.ranked
