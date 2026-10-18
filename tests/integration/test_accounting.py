from datetime import datetime, timezone
from pathlib import Path

from deal_grandpa.adapters.fixture_source import FixtureSource
from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.orchestrator import DealGrandpaService


QUERY = "I need a phone under 30000 with 8GB RAM 128GB storage"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def test_fixture_raw_count_is_180():
    loaded = FixtureSource("tests/fixtures/golden_offers.json").load()
    assert len(loaded.offers) == 180


def test_deduplication_keeps_178_and_only_removes_duplicate_group(tmp_path):
    loaded = FixtureSource("tests/fixtures/golden_offers.json").load()
    service = DealGrandpaService(SQLiteStore(tmp_path / "accounting.sqlite3"), "tests/fixtures/golden_offers.json")
    deduplicated = service._deduplicate(loaded.offers)
    assert len(deduplicated) == 178

    duplicate_ids = {"offer-0001", "offer-0011", "offer-0012"}
    removed_ids = {offer.offer_id for offer in loaded.offers} - {offer.offer_id for offer in deduplicated}
    assert removed_ids == {"offer-0011", "offer-0012"}
    assert duplicate_ids <= {offer.offer_id for offer in loaded.offers}


def test_demo_accounting_is_three_ranked_and_175_excluded(tmp_path):
    store = SQLiteStore(tmp_path / "accounting-demo.sqlite3")
    service = DealGrandpaService(store, "tests/fixtures/golden_offers.json")
    decision = service.run(QUERY, now=NOW)
    assert len(decision.ranked) == 3
    assert len(decision.excluded) == 175
    assert len(decision.ranked) + len(decision.excluded) == 178
