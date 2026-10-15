from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deal_grandpa.domain.models import Money, Offer, SourceInfo
from deal_grandpa.domain.policy import normalized_variant, parse_timestamp
from deal_grandpa.schemas import FixtureInput


class SourceError(RuntimeError):
    """Connector failure that is eligible for the narrow retry policy."""


@dataclass(frozen=True)
class LoadedFixture:
    fixture_id: str
    raw_payload: dict[str, Any]
    sources: tuple[SourceInfo, ...]
    offers: tuple[Offer, ...]


class FixtureSource:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._timeout_used = False

    def load(self, fault_mode: str | None = None) -> LoadedFixture:
        if fault_mode == "persistent_failure":
            raise SourceError("fixture connector failure: persistent_failure")
        if fault_mode == "timeout_once" and not self._timeout_used:
            self._timeout_used = True
            raise SourceError("fixture connector timeout: timeout_once")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        fixture = FixtureInput.model_validate(raw)
        sources_by_id = {source.source_id: source for source in fixture.sources}
        sources = tuple(
            SourceInfo(
                source_id=source.source_id,
                domain=source.domain,
                approved=source.approved,
                reliability=source.reliability,
                retention_days=source.retention_days,
                breaker_state=source.breaker_state,
                dead_letter_count=source.dead_letter_count,
                field_permissions=tuple(source.field_permissions),
            )
            for source in fixture.sources
        )
        normalized: list[Offer] = []
        for item in fixture.offers:
            if item.source_id not in sources_by_id:
                raise ValueError(f"offer {item.offer_id} references unknown source")
            source = next(source for source in sources if source.source_id == item.source_id)
            if item.shipping.currency != item.currency:
                # Keep the record visible for the currency gate rather than silently converting it.
                shipping_currency = item.shipping.currency
            else:
                shipping_currency = item.currency
            shipping = (
                Money.from_minor(item.shipping.amount_minor, shipping_currency)
                if item.shipping.amount_minor is not None else None
            )
            list_price = Money.from_minor(item.list_price_minor, item.currency) if item.list_price_minor is not None else None
            discount = Money.from_minor(item.verified_discount_minor, item.currency) if item.verified_discount_minor is not None else None
            normalized.append(
                Offer(
                    offer_id=item.offer_id,
                    source=source,
                    merchant=item.merchant,
                    url=item.url,
                    domain=item.domain,
                    title=item.title,
                    variant=normalized_variant(item.category, item.brand, item.model, item.variant.model_dump()),
                    condition=item.condition,
                    price=Money.from_minor(item.price_minor, item.currency),
                    shipping_status=item.shipping.status,
                    shipping=shipping,
                    list_price=list_price,
                    verified_discount=discount,
                    promo_starts_at=parse_timestamp(item.promo_starts_at) if item.promo_starts_at else None,
                    promo_valid_until=parse_timestamp(item.promo_valid_until) if item.promo_valid_until else None,
                    source_ts=parse_timestamp(item.source_ts),
                    fetched_at=parse_timestamp(item.fetched_at),
                    update_model=item.update_model,
                    availability=item.availability,
                    gtin=item.gtin,
                    mpn=item.mpn,
                    country=item.country,
                    redirect_url=item.redirect_url,
                    source_record_id=item.source_record_id,
                )
            )
        return LoadedFixture(fixture.fixture_id, raw, sources, tuple(normalized))
