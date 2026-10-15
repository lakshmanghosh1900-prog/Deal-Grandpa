from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    ABSTAIN = "ABSTAIN"


class Outcome(str, Enum):
    RECOMMEND = "RECOMMEND"
    WITH_CAVEATS = "WITH_CAVEATS"
    ABSTAIN = "ABSTAIN"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    QUARANTINE = "quarantine"
    EXPIRE = "expire"
    ROLLBACK = "rollback"
    NOTIFY = "notify"


class PermissionState(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    REVOKED = "revoked"


_MINOR_UNITS = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}


def minor_units(currency: str) -> int:
    return _MINOR_UNITS.get(currency.upper(), 2)


@dataclass(frozen=True)
class Money:
    """Exact money value. Persistence uses ``minor``; arithmetic stays Decimal."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            raise TypeError("Money.amount must be Decimal")
        currency = self.currency.upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter code")
        if not self.amount.is_finite() or self.amount < Decimal("0"):
            raise ValueError("money must be finite and non-negative")
        scale = Decimal(1).scaleb(-minor_units(currency))
        if self.amount != self.amount.quantize(scale):
            raise ValueError("money has more minor units than its currency allows")
        object.__setattr__(self, "currency", currency)

    @classmethod
    def from_minor(cls, amount_minor: int, currency: str) -> "Money":
        if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
            raise TypeError("minor amount must be an integer")
        scale = Decimal(1).scaleb(-minor_units(currency))
        return cls((Decimal(amount_minor) * scale).quantize(scale), currency)

    def to_minor(self) -> int:
        scale = Decimal(1).scaleb(-minor_units(self.currency))
        return int((self.amount / scale).to_integral_exact(rounding=ROUND_HALF_UP))

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("cannot add different currencies")
        return Money(self.amount + other.amount, self.currency)

    def __lt__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("cannot compare different currencies")
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        if self.currency != other.currency:
            raise ValueError("cannot compare different currencies")
        return self.amount <= other.amount

    def __sub__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("cannot subtract different currencies")
        if self.amount < other.amount:
            raise ValueError("money cannot become negative")
        return Money(self.amount - other.amount, self.currency)


@dataclass(frozen=True)
class Variant:
    category: str
    brand: str
    model: str
    ram_gb: int | None
    storage_gb: int | None
    network: str | None
    pack_count: int
    color: str | None

    def key(self) -> tuple[Any, ...]:
        return (
            self.category, self.brand, self.model, self.ram_gb, self.storage_gb,
            self.network, self.pack_count, self.color,
        )

    def label(self) -> str:
        parts = [self.brand, self.model]
        if self.ram_gb is not None:
            parts.append(f"{self.ram_gb}GB RAM")
        if self.storage_gb is not None:
            parts.append(f"{self.storage_gb}GB storage")
        if self.network:
            parts.append(self.network)
        if self.pack_count != 1:
            parts.append(f"pack of {self.pack_count}")
        return " ".join(parts)


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    domain: str
    approved: bool
    reliability: int
    retention_days: int
    breaker_state: str = "closed"
    dead_letter_count: int = 0
    field_permissions: tuple[str, ...] = ()
    revoked_fields: tuple[str, ...] = ()

    def permission_state(self, field: str) -> PermissionState:
        if field in self.revoked_fields:
            return PermissionState.REVOKED
        if field in self.field_permissions:
            return PermissionState.ALLOWED
        return PermissionState.DENIED


@dataclass(frozen=True)
class Offer:
    offer_id: str
    source: SourceInfo
    merchant: str
    url: str
    domain: str
    title: str
    variant: Variant
    condition: str
    price: Money
    shipping_status: str
    shipping: Money | None
    list_price: Money | None
    verified_discount: Money | None
    promo_starts_at: datetime | None
    promo_valid_until: datetime | None
    source_ts: datetime
    fetched_at: datetime
    update_model: str
    availability: str
    gtin: str | None
    mpn: str | None
    country: str
    redirect_url: str | None
    source_record_id: str
    permission_failures: tuple[tuple[str, PermissionState], ...] = ()

    def effective_cost(self) -> Money | None:
        if self.shipping_status != "known" or self.shipping is None:
            return None
        if self.price.currency != self.shipping.currency:
            return None
        return self.price + self.shipping


@dataclass(frozen=True)
class SearchQuery:
    raw: str
    category: str | None
    budget: Money | None
    ram_gb: int | None
    storage_gb: int | None
    network: str | None
    condition: str = "new"
    country: str = "IN"


@dataclass(frozen=True)
class GateResult:
    gate: str
    status: GateStatus
    reason_code: str
    detail: str


@dataclass(frozen=True)
class EvidenceNumber:
    name: str
    value: str
    source_ts: str
    fetched_at: str
    rules_hash: str
    run_id: str


@dataclass(frozen=True)
class OfferEvaluation:
    offer: Offer
    gates: tuple[GateResult, ...]
    effective_cost: Money | None
    trust_score: int | None
    evidence_numbers: tuple[EvidenceNumber, ...]
    explanation: str
    recovery: tuple[RecoveryAction, ...] = ()

    @property
    def eligible(self) -> bool:
        return bool(self.effective_cost) and all(g.status is GateStatus.PASS for g in self.gates)

    @property
    def has_caveat(self) -> bool:
        return any(g.status is GateStatus.ABSTAIN for g in self.gates)


@dataclass(frozen=True)
class ClusterStats:
    cohort: str
    count: int
    median_minor: int | None
    mad_minor: int | None
    condition: str = "new"


@dataclass(frozen=True)
class Decision:
    run_id: str
    query: SearchQuery
    outcome: Outcome
    selected_cohort: str | None
    ranked: tuple[OfferEvaluation, ...]
    excluded: tuple[OfferEvaluation, ...]
    cluster_stats: tuple[ClusterStats, ...]
    recommendation: OfferEvaluation | None
    reasons: tuple[str, ...]
    rules_hash: str
    source_registry_snapshot: tuple[SourceInfo, ...]

    @property
    def verified_count(self) -> int:
        return sum(1 for item in self.ranked if item.eligible)


def format_money(money: Money | None) -> str:
    if money is None:
        return "unknown"
    symbols = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
    return f"{symbols.get(money.currency, money.currency + ' ')}{money.amount:.2f}"
