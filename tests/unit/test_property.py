import itertools
from decimal import Decimal

from deal_grandpa.domain.canonical import canonical_hash
from deal_grandpa.domain.models import Money


def test_key_order_permutations_have_one_replay_hash():
    items = [("a", 1), ("b", {"z": 2, "y": 3}), ("c", [3, 2, 1])]
    hashes = {canonical_hash(dict(order)) for order in itertools.permutations(items)}
    assert len(hashes) == 1


def test_decimal_minor_round_trip_property_for_representative_amounts():
    for minor in range(0, 10000, 137):
        money = Money(Decimal(minor) / Decimal(100), "INR")
        assert Money.from_minor(money.to_minor(), "INR") == money
