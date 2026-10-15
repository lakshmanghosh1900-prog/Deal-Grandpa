import pytest
from pydantic import ValidationError

from deal_grandpa.schemas import OfferInput


def test_boundary_is_strict_for_money():
    with pytest.raises(ValidationError):
        OfferInput.model_validate({"offer_id":"x", "source_id":"s", "merchant":"m", "url":"u", "domain":"d", "title":"t", "category":"phone", "brand":"b", "model":"m", "variant":{"ram_gb":8,"storage_gb":128,"network":"5g","pack_count":1,"color":"blue"}, "condition":"new", "price_minor":"2599000", "currency":"INR", "shipping":{"status":"known","amount_minor":0,"currency":"INR"}, "source_ts":"2026-10-16T10:00:00Z", "fetched_at":"2026-10-16T10:00:00Z", "update_model":"daily", "availability":"in_stock", "country":"IN", "source_record_id":"r"})
