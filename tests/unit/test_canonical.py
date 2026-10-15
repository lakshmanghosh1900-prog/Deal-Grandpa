import pytest

from deal_grandpa.domain.canonical import canonical_bytes, canonical_hash


def test_rfc8785_golden_vector():
    payload = {
        "observations": [{
            "source_id": "shop-a",
            "observed_at": "2026-01-15T11:00:00Z",
            "price": {"currency": "USD", "amount": "19.99"},
            "variant": {"cohort": "acme|widget-7", "attrs": {"size": "10", "color": "red"}},
        }],
        "clock": "2026-01-15T12:00:00Z",
        "ruleset": "dg-test-1",
    }
    assert canonical_bytes(payload).decode() == '{"clock":"2026-01-15T12:00:00Z","observations":[{"observed_at":"2026-01-15T11:00:00Z","price":{"amount":"19.99","currency":"USD"},"source_id":"shop-a","variant":{"attrs":{"color":"red","size":"10"},"cohort":"acme|widget-7"}}],"ruleset":"dg-test-1"}'
    assert canonical_hash(payload) == "e8a5df1ec3852b4a76771b6b4550c35785c4a1ce9242cad7b73a771c8272e616"


def test_jcs_normalizes_numbers_and_unicode():
    payload = {"b": 1.0, "a": -0, "u": "é", "arr": [3, 2, 1]}
    assert canonical_bytes(payload).decode() == '{"a":0,"arr":[3,2,1],"b":1,"u":"é"}'
    assert canonical_hash(payload) == "fa9690788cdc1eb057fb231c3c74890be6a4621b6ed665018723a8c4f6747a2d"
    with pytest.raises(ValueError):
        canonical_bytes({"bad": float("nan")})
