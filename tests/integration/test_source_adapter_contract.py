from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from deal_grandpa.adapters.contracts import SourceAdapter, SourceAdapterError
from deal_grandpa.adapters.fixture_source import FixtureSource, LoadedFixture
from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.domain.models import Outcome
from deal_grandpa.orchestrator import DealGrandpaService


def test_fixture_adapter_matches_source_contract_and_returns_metadata():
    adapter = FixtureSource("tests/fixtures/golden_offers.json")
    assert isinstance(adapter, SourceAdapter)
    loaded = adapter.load()
    assert loaded.sources
    assert all(source.source_id and source.domain for source in loaded.sources)
    assert all(offer.source.source_id for offer in loaded.offers)


def test_schema_failure_is_not_converted_to_source_success(tmp_path):
    import json

    payload = json.loads(Path("tests/fixtures/golden_offers.json").read_text(encoding="utf-8"))
    payload["offers"][0].pop("price_minor")
    fixture = tmp_path / "invalid.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        FixtureSource(fixture).load()


def test_missing_source_metadata_fails_closed(tmp_path):
    import json

    payload = json.loads(Path("tests/fixtures/golden_offers.json").read_text(encoding="utf-8"))
    payload["sources"][0]["source_id"] = ""
    fixture = tmp_path / "missing-metadata.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        FixtureSource(fixture).load()


def test_source_failure_uses_adapter_error_contract():
    with pytest.raises(SourceAdapterError):
        FixtureSource("tests/fixtures/golden_offers.json").load("persistent_failure")


class AlwaysFailAdapter:
    name = "controlled-failure"

    def load(self, fault_mode=None) -> LoadedFixture:
        raise SourceAdapterError("controlled source failure")


def test_injected_source_failure_abstains_without_fixture_fallback(tmp_path):
    store = SQLiteStore(tmp_path / "failure.sqlite3")
    service = DealGrandpaService(
        store,
        str(tmp_path / "fixture-must-not-be-read.json"),
        clock=lambda: datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc),
        source_adapter=AlwaysFailAdapter(),
    )
    decision = service.run("I need a phone under 30000 with 8GB RAM 128GB storage", now=datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc))
    assert decision.outcome is Outcome.ABSTAIN
    assert decision.recommendation is None
    assert not decision.ranked
    assert any("quarantine" in reason for reason in decision.reasons)
