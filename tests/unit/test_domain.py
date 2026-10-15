from decimal import Decimal

import pytest

from deal_grandpa.domain.models import Money
from deal_grandpa.domain.policy import (
    cluster_statistics,
    modified_z,
    parse_query,
    valid_gtin,
)


def test_money_is_exact_minor_unit_decimal():
    one = Money(Decimal("0.10"), "USD")
    two = Money(Decimal("0.20"), "USD")
    assert (one + two).amount == Decimal("0.30")
    assert (one + two).to_minor() == 30
    assert Money.from_minor(2599900, "INR").amount == Decimal("25999.00")


def test_money_rejects_extra_precision_and_currency_mismatch():
    with pytest.raises(ValueError):
        Money(Decimal("1.005"), "USD")
    with pytest.raises(ValueError):
        Money(Decimal("1.00"), "USD") + Money(Decimal("1.00"), "INR")


def test_query_parser_keeps_budget_in_default_inr():
    query = parse_query("I need a phone under 30000 with 8GB RAM 128GB storage")
    assert query.budget.amount == Decimal("30000")
    assert query.budget.currency == "INR"
    assert query.ram_gb == 8 and query.storage_gb == 128


def test_gtin_checksum_is_tier_one_but_missing_is_allowed_for_tier_two():
    assert valid_gtin("8901030000010")
    assert not valid_gtin("1234567890123")
    assert valid_gtin(None)


def test_median_mad_and_zero_mad_are_deterministic():
    assert modified_z(18000, type("Stats", (), {"median_minor":10000, "mad_minor":100})()) == Decimal("53.96")
    assert modified_z(100, type("Stats", (), {"median_minor":100, "mad_minor":0})()) == Decimal("0")
    assert modified_z(101, type("Stats", (), {"median_minor":100, "mad_minor":0})()) is None
