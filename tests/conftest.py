from datetime import datetime, timezone
from pathlib import Path

import pytest

from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.orchestrator import DealGrandpaService

FIXTURE = Path(__file__).parent / "fixtures" / "golden_offers.json"
FIXED_NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)

@pytest.fixture
def service(tmp_path):
    store = SQLiteStore(tmp_path / "test.sqlite3")
    service = DealGrandpaService(store, str(FIXTURE), clock=lambda: FIXED_NOW)
    yield service
    store.close()
