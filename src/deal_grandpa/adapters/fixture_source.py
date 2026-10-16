from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deal_grandpa.domain.models import Money, Offer, PermissionState, SourceInfo, Variant
from deal_grandpa.domain.policy import REQUIRED_PERMISSION_FIELDS, normalized_variant, parse_timestamp
from deal_grandpa.schemas import FixtureInput
from .contracts import SourceAdapterError


class SourceError(SourceAdapterError):
    """Connector failure that is eligible for the narrow retry policy."""


@dataclass(frozen=True)
class LoadedFixture:
    fixture_id: str
    raw_payload: dict[str, Any]
    sources: tuple[SourceInfo, ...]
    offers: tuple[Offer, ...]


class FixtureSource:
    name = "fixture"
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
        return self.load_payload(raw)

    @classmethod
    def load_payload(cls, raw: dict[str, Any]) -> LoadedFixture:
        overrides = {
            item.get("offer_id"): tuple(
                (failure["field"], PermissionState(failure["state"]))
                for failure in item.get("_permission_failures", [])
            )
            for item in raw.get("offers", [])
            if item.get("_permission_failures")
        }
        clean = {
            **raw,
            "offers": [
                {key: value for key, value in item.items() if key != "_permission_failures"}
                for item in raw.get("offers", [])
            ],
        }
        fixture = FixtureInput.model_validate(clean)
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
                revoked_fields=tuple(source.revoked_fields),
            )
            for source in fixture.sources
        )
        normalized: list[Offer] = []
        sanitized_offers: list[dict[str, Any]] = []
        for item in fixture.offers:
            if item.source_id not in sources_by_id:
                raise ValueError(f"offer {item.offer_id} references unknown source")
            source = next(source for source in sources if source.source_id == item.source_id)
            failures = overrides.get(item.offer_id, cls._permission_failures(item, source))
            sanitized_offers.append(cls._sanitize_offer(item, source, failures))
            if failures:
                normalized.append(cls._permission_safe_offer(item.offer_id, source, failures))
                continue
            shipping_currency = item.shipping.currency
            shipping = (
                Money.from_minor(item.shipping.amount_minor, shipping_currency)
                if item.shipping.amount_minor is not None else None
            )
            fees = Money.from_minor(item.fees.amount_minor, item.currency) if item.fees.status == "known" else None
            list_price = Money.from_minor(item.list_price_minor, item.currency) if item.list_price_minor is not None else None
            discount = Money.from_minor(item.verified_discount_minor, item.currency) if item.verified_discount_minor is not None else None
            normalized.append(
                Offer(
                    offer_id=item.offer_id,
                    source=source,
                    merchant=item.merchant,
                    url=item.url,
                    domain=source.domain,
                    title=item.title if source.permission_state("title") is PermissionState.ALLOWED else "",
                    variant=normalized_variant(item.category, item.brand, item.model, item.variant.model_dump()),
                    condition=item.condition,
                    price=Money.from_minor(item.price_minor, item.currency),
                    shipping_status=item.shipping.status,
                    shipping=shipping,
                    fees_status=item.fees.status,
                    fees=fees,
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
                    source_record_id=item.source_record_id if source.permission_state("source_record_id") is PermissionState.ALLOWED else "",
                    permission_failures=failures,
                )
            )
        sanitized = {**raw, "sources": [source.model_dump() for source in fixture.sources], "offers": sanitized_offers}
        return LoadedFixture(fixture.fixture_id, sanitized, sources, tuple(normalized))

    @staticmethod
    def _permission_failures(item: Any, source: SourceInfo) -> tuple[tuple[str, PermissionState], ...]:
        fields = list(REQUIRED_PERMISSION_FIELDS)
        if item.gtin is not None:
            fields.append("gtin")
        for field, value in (("redirect_url", item.redirect_url), ("promo_starts_at", item.promo_starts_at),
                             ("promo_valid_until", item.promo_valid_until), ("list_price_minor", item.list_price_minor),
                             ("verified_discount_minor", item.verified_discount_minor)):
            if value is not None:
                fields.append(field)
        return tuple((field, source.permission_state(field)) for field in fields if source.permission_state(field) is not PermissionState.ALLOWED)

    @staticmethod
    def _sanitize_offer(item: Any, source: SourceInfo, failures: tuple[tuple[str, PermissionState], ...]) -> dict[str, Any]:
        record = item.model_dump()
        states = dict(failures)
        defaults: dict[str, Any] = {
            "merchant": "", "title": "", "category": "", "brand": "", "model": "",
            "variant": {"ram_gb": None, "storage_gb": None, "network": None, "pack_count": 1, "color": None},
            "condition": "new", "price_minor": 0, "currency": "INR",
            "shipping": {"status": "unknown", "amount_minor": None, "currency": "INR"},
            "fees": {"status": "unknown", "amount_minor": 0},
            "list_price_minor": None, "verified_discount_minor": None, "promo_starts_at": None,
            "promo_valid_until": None, "source_ts": "1970-01-01T00:00:00Z", "fetched_at": "1970-01-01T00:00:00Z",
            "update_model": "daily", "availability": "out_of_stock", "gtin": None, "mpn": None,
            "country": "", "url": "", "redirect_url": None, "source_record_id": "",
        }
        protected_fields = tuple(defaults)
        for field in protected_fields:
            if source.permission_state(field) is not PermissionState.ALLOWED:
                record[field] = defaults[field]
        record["domain"] = source.domain
        if failures:
            record["_permission_failures"] = [{"field": field, "state": state.value} for field, state in failures]
        return record

    @staticmethod
    def _permission_safe_offer(offer_id: str, source: SourceInfo, failures: tuple[tuple[str, PermissionState], ...]) -> Offer:
        epoch = parse_timestamp("1970-01-01T00:00:00Z")
        return Offer(
            offer_id=offer_id, source=source, merchant="", url="", domain=source.domain, title="",
            variant=Variant("", "", "", None, None, None, 1, None), condition="new",
            price=Money.from_minor(0, "INR"), shipping_status="unknown", shipping=None, fees_status="unknown", fees=None, list_price=None,
            verified_discount=None, promo_starts_at=None, promo_valid_until=None, source_ts=epoch,
            fetched_at=epoch, update_model="daily", availability="out_of_stock", gtin=None, mpn=None,
            country="", redirect_url=None, source_record_id="", permission_failures=failures,
        )
