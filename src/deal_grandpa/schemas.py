from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

KNOWN_PERMISSION_FIELDS = frozenset({
    "merchant", "title", "category", "brand", "model", "variant", "condition",
    "price_minor", "currency", "shipping", "fees", "list_price_minor", "verified_discount_minor",
    "promo_starts_at", "promo_valid_until", "source_ts", "fetched_at", "update_model",
    "availability", "gtin", "mpn", "country", "url", "redirect_url", "source_record_id",
})


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VariantInput(StrictSchema):
    ram_gb: StrictInt | None = Field(default=None, ge=1, le=1024)
    storage_gb: StrictInt | None = Field(default=None, ge=1, le=16384)
    network: StrictStr | None = None
    pack_count: StrictInt = Field(default=1, ge=1, le=100)
    color: StrictStr | None = None


class ShippingInput(StrictSchema):
    status: Literal["known", "unknown"]
    amount_minor: StrictInt | None = Field(default=None, ge=0)
    currency: StrictStr

    @field_validator("amount_minor")
    @classmethod
    def amount_matches_status(cls, value: StrictInt | None, info):
        status = info.data.get("status")
        if status == "known" and value is None:
            raise ValueError("known shipping needs amount_minor")
        if status == "unknown" and value is not None:
            raise ValueError("unknown shipping cannot carry an amount")
        return value


class FeesInput(StrictSchema):
    status: Literal["known", "unknown"] = "known"
    amount_minor: StrictInt = Field(default=0, ge=0)

    @model_validator(mode="after")
    def unknown_fees_have_no_amount(self):
        if self.status == "unknown" and self.amount_minor != 0:
            raise ValueError("unknown fees cannot carry an amount")
        return self


class SourceInput(StrictSchema):
    source_id: StrictStr = Field(min_length=1, max_length=200)
    domain: StrictStr = Field(min_length=1, max_length=253)
    approved: StrictBool
    reliability: StrictInt = Field(ge=0, le=100)
    retention_days: StrictInt = Field(ge=1, le=3650)
    breaker_state: Literal["closed", "open", "half_open"] = "closed"
    dead_letter_count: StrictInt = Field(default=0, ge=0)
    field_permissions: list[StrictStr] = Field(default_factory=list)
    revoked_fields: list[StrictStr] = Field(default_factory=list)

    @field_validator("field_permissions", "revoked_fields")
    @classmethod
    def permission_fields_are_known(cls, value: list[StrictStr]):
        if len(value) != len(set(value)):
            raise ValueError("permission fields must be unique")
        unknown = set(value) - KNOWN_PERMISSION_FIELDS
        if unknown:
            raise ValueError("unknown permission field")
        return value

    @model_validator(mode="after")
    def permissions_are_disjoint(self):
        if set(self.field_permissions) & set(self.revoked_fields):
            raise ValueError("a field cannot be both permitted and revoked")
        return self


class OfferInput(StrictSchema):
    offer_id: StrictStr
    source_id: StrictStr
    merchant: StrictStr
    url: StrictStr
    domain: StrictStr
    title: StrictStr
    category: StrictStr
    brand: StrictStr
    model: StrictStr
    variant: VariantInput
    condition: Literal["new", "refurbished", "open_box"]
    price_minor: StrictInt = Field(ge=0)
    currency: StrictStr
    shipping: ShippingInput
    fees: FeesInput = FeesInput()
    list_price_minor: StrictInt | None = Field(default=None, ge=0)
    verified_discount_minor: StrictInt | None = Field(default=None, ge=0)
    promo_starts_at: StrictStr | None = None
    promo_valid_until: StrictStr | None = None
    source_ts: StrictStr
    fetched_at: StrictStr
    update_model: Literal["hourly", "daily", "weekly", "push_realtime"]
    availability: Literal["in_stock", "out_of_stock", "preorder"]
    gtin: StrictStr | None = None
    mpn: StrictStr | None = None
    country: StrictStr
    redirect_url: StrictStr | None = None
    source_record_id: StrictStr


class FixtureInput(StrictSchema):
    fixture_id: StrictStr
    sources: list[SourceInput]
    offers: list[OfferInput]


class QueryInput(StrictSchema):
    query: StrictStr = Field(min_length=1, max_length=500)


class FaultInput(StrictSchema):
    mode: Literal["timeout_once", "persistent_failure", "clear"]


class GateOutput(StrictSchema):
    gate: StrictStr
    status: StrictStr
    reason_code: StrictStr
    detail: StrictStr


class NumberProvenanceOutput(StrictSchema):
    name: StrictStr
    value: StrictStr
    source_ts: StrictStr
    fetched_at: StrictStr
    rules_hash: StrictStr
    run_id: StrictStr


class EvidenceCardOutput(StrictSchema):
    template: Literal["evidence-card-v1"]
    offer_id: StrictStr
    merchant: StrictStr | None
    variant: StrictStr | None
    price_minor: StrictInt | None
    shipping_minor: StrictInt | None
    effective_cost_minor: StrictInt | None
    item_price_minor: StrictInt | None
    shipping_cost_minor: StrictInt | None
    verified_discount_minor: StrictInt | None
    known_fees_minor: list[StrictInt]
    fees_status: StrictStr
    effective_total_minor: StrictInt | None
    currency: StrictStr | None
    price_display: StrictStr | None
    shipping_display: StrictStr | None
    fees_display: StrictStr | None
    effective_cost_display: StrictStr | None
    trust_score: StrictInt | None
    gates: list[GateOutput]
    numbers: list[NumberProvenanceOutput]
    explanation: StrictStr


class RunOutput(StrictSchema):
    run_id: StrictStr
    outcome: StrictStr
    query: StrictStr
    selected_cohort: StrictStr | None
    recommendation: EvidenceCardOutput | None
    ranked: list[EvidenceCardOutput]
    excluded: list[EvidenceCardOutput]
    reasons: list[StrictStr]
    policy_version: StrictStr
    review_version: StrictStr
    rules_hash: StrictStr
    source_health: list[dict]
