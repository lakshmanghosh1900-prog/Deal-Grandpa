from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable
from urllib.parse import urlsplit

from .models import (
    ClusterStats,
    EvidenceNumber,
    GateResult,
    GateStatus,
    Money,
    Offer,
    OfferEvaluation,
    PermissionState,
    SearchQuery,
    SourceInfo,
    Variant,
)

RULESET_VERSION = "dg-0.1-gates-1-7"
REVIEW_VERSION = "dg-review-0"
MAX_ANOMALY_Z = Decimal("3.5")
ANOMALY_FACTOR = Decimal("0.6745")
REQUIRED_PERMISSION_FIELDS = (
    "variant", "condition", "price_minor", "currency", "shipping", "fees", "source_ts",
    "fetched_at", "availability", "country", "url", "merchant", "mpn",
)


def policy_manifest() -> dict[str, object]:
    return {
        "policy_version": RULESET_VERSION,
        "review_version": REVIEW_VERSION,
        "max_anomaly_z": str(MAX_ANOMALY_Z),
        "anomaly_factor": str(ANOMALY_FACTOR),
        "freshness_hours": {"hourly": "6", "daily": "24", "weekly": "168", "push_realtime": "0.25"},
        "trust_weights": [35, 20, 15, 10, 20],
        "gate_order": ["G1", "G2", "G3", "G4", "G5", "G6", "G7"],
    }


def normalize_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def parse_timestamp(value: str) -> datetime:
    clean = value.strip()
    if clean.endswith("Z"):
        clean = clean[:-1] + "+00:00"
    parsed = datetime.fromisoformat(clean)
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_variant(
    category: str,
    brand: str,
    model: str,
    attrs: dict[str, object],
) -> Variant:
    ram = attrs.get("ram_gb")
    storage = attrs.get("storage_gb")
    network = attrs.get("network")
    pack = attrs.get("pack_count", 1)
    color = attrs.get("color")
    return Variant(
        category=normalize_text(category),
        brand=normalize_text(brand),
        model=normalize_text(model),
        ram_gb=int(ram) if ram is not None else None,
        storage_gb=int(storage) if storage is not None else None,
        network=normalize_text(str(network)) if network is not None else None,
        pack_count=int(pack),
        color=normalize_text(str(color)) if color is not None else None,
    )


def cohort_key(variant: Variant) -> str:
    fields = (
        variant.category,
        variant.brand,
        variant.model,
        "ram=" + (str(variant.ram_gb) if variant.ram_gb is not None else "missing"),
        "storage=" + (str(variant.storage_gb) if variant.storage_gb is not None else "missing"),
        "network=" + (variant.network or "missing"),
        "pack=" + str(variant.pack_count),
        "color=" + (variant.color or "missing"),
    )
    return "|".join(fields)


def parse_query(raw: str) -> SearchQuery:
    text = normalize_text(raw)
    category = "phone" if re.search(r"\b(phone|smartphone|mobile)\b", text) else None
    ram_match = re.search(r"(\d+)\s*gb\s*ram", text)
    storage_match = re.search(r"(\d+)\s*gb\s*(?:storage|rom)", text)
    budget_match = re.search(r"(?:under|below|less than|max(?:imum)?)\s*([\d,]+(?:\.\d+)?)", text)
    if budget_match is None:
        budget_match = re.search(r"(?:₹|inr|usd|\$)\s*([\d,]+(?:\.\d+)?)", text)
    currency = "INR"
    if "$" in raw or re.search(r"\busd\b", text):
        currency = "USD"
    elif re.search(r"\beur\b|€", text):
        currency = "EUR"
    budget = None
    if budget_match:
        amount = Decimal(budget_match.group(1).replace(",", ""))
        budget = Money(amount, currency)
    network = None
    if re.search(r"\b5g\b", text):
        network = "5g"
    elif re.search(r"\b4g\b", text):
        network = "4g"
    return SearchQuery(
        raw=raw,
        category=category,
        budget=budget,
        ram_gb=int(ram_match.group(1)) if ram_match else None,
        storage_gb=int(storage_match.group(1)) if storage_match else None,
        network=network,
    )


def valid_gtin(value: str | None) -> bool:
    if value is None:
        return True
    if not value.isdigit() or len(value) not in {8, 12, 13, 14}:
        return False
    digits = [int(item) for item in value]
    check = digits.pop()
    total = 0
    for position, digit in enumerate(reversed(digits)):
        total += digit * (3 if position % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == check


def normalize_hostname(hostname: str | None) -> str | None:
    if not hostname:
        return None
    value = hostname.rstrip(".").casefold()
    try:
        return value.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def validate_https_url(url: str, approved_domain: str) -> tuple[bool, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False, "INVALID_MERCHANT_URL"
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        return False, "INVALID_MERCHANT_URL"
    if "@" in parsed.netloc:
        return False, "INVALID_MERCHANT_URL"
    if port not in (None, 443):
        return False, "INVALID_MERCHANT_URL"
    if normalize_hostname(parsed.hostname) != normalize_hostname(approved_domain):
        return False, "URL_DOMAIN_MISMATCH"
    return True, "URL_VALID"


def variant_matches_query(offer: Offer, query: SearchQuery) -> bool:
    variant = offer.variant
    if query.category and variant.category != normalize_text(query.category):
        return False
    if query.ram_gb is not None and variant.ram_gb != query.ram_gb:
        return False
    if query.storage_gb is not None and variant.storage_gb != query.storage_gb:
        return False
    if query.network is not None and variant.network != normalize_text(query.network):
        return False
    return True


def age_limit(update_model: str) -> timedelta:
    limits = {
        "hourly": timedelta(hours=6),
        "daily": timedelta(days=1),
        "weekly": timedelta(days=7),
        "push_realtime": timedelta(minutes=15),
    }
    return limits.get(update_model, timedelta(hours=6))


def median(values: list[int]) -> int:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def cluster_statistics(offers: Iterable[Offer]) -> dict[tuple[str, str], ClusterStats]:
    values: dict[tuple[str, str], list[int]] = defaultdict(list)
    seen: set[tuple[str, str, str, int]] = set()
    for offer in offers:
        cost = offer.effective_cost()
        if cost is None:
            continue
        lane = (cohort_key(offer.variant), offer.condition)
        fingerprint = (offer.source.source_id, lane[0], lane[1], cost.to_minor())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        values[lane].append(cost.to_minor())
    result: dict[tuple[str, str], ClusterStats] = {}
    for (cohort, condition), costs in values.items():
        med = median(costs)
        deviations = [abs(cost - med) for cost in costs]
        result[(cohort, condition)] = ClusterStats(cohort, len(costs), med, median(deviations), condition)
    return result


def modified_z(cost_minor: int, stats: ClusterStats) -> Decimal | None:
    if stats.median_minor is None or stats.mad_minor is None:
        return None
    if stats.mad_minor == 0:
        return Decimal("0") if cost_minor == stats.median_minor else None
    return ANOMALY_FACTOR * Decimal(abs(cost_minor - stats.median_minor)) / Decimal(stats.mad_minor)


def _gate(gate: str, status: GateStatus, reason: str, detail: str) -> GateResult:
    return GateResult(gate, status, reason, detail)


def _initial_gates(offer: Offer, query: SearchQuery, now: datetime) -> tuple[GateResult, ...]:
    permission_failures = offer.permission_failures
    if any(state is PermissionState.REVOKED for _, state in permission_failures):
        permission_reason = "PERMISSION_REVOKED"
    elif permission_failures:
        permission_reason = "PERMISSION_DENIED"
    else:
        permission_reason = None
    variant_ok = variant_matches_query(offer, query)
    exact_lane = variant_ok and offer.condition == query.condition
    if permission_reason:
        g1_status = GateStatus.FAIL
        g1_reason = permission_reason
        g1_detail = "A required source field is not available for this decision"
    else:
        g1_status = GateStatus.PASS if exact_lane else GateStatus.FAIL
        g1_reason = "EXACT_VARIANT" if exact_lane else ("VARIANT_MISMATCH" if not variant_ok else "CONDITION_LANE_MISMATCH")
        g1_detail = "All requested variant attributes match" if exact_lane else ("The product or one requested attribute differs" if not variant_ok else "The offer is in a different condition lane")
    g1 = _gate(
        "G1",
        g1_status,
        g1_reason,
        g1_detail,
    )
    identity_ok = valid_gtin(offer.gtin) and bool(offer.variant.brand and offer.variant.model)
    g2 = _gate(
        "G2",
        GateStatus.PASS if identity_ok else GateStatus.FAIL,
        "IDENTITY_CHECKED" if identity_ok else "INVALID_GTIN",
        "GTIN checksum or brand/model identity is valid" if identity_ok else "The source identity could not be verified",
    )
    price_ok = offer.price.currency == (query.budget.currency if query.budget else offer.price.currency)
    if price_ok and query.budget:
        cost = offer.effective_cost()
        price_ok = cost is not None and cost.currency == query.budget.currency and cost <= query.budget
    currency_mismatch = bool(query.budget and offer.price.currency != query.budget.currency)
    if offer.shipping is not None and offer.shipping.currency != offer.price.currency:
        currency_mismatch = True
    g3_reason = "PRICE_WITHIN_BUDGET" if price_ok else ("CURRENCY_MISMATCH" if currency_mismatch else "OVER_BUDGET")
    g3 = _gate("G3", GateStatus.PASS if price_ok else GateStatus.FAIL, g3_reason, "Price is exact minor-unit money" if price_ok else "Price cannot be verified against the request")

    if offer.source_ts > now:
        g4 = _gate("G4", GateStatus.ABSTAIN, "FUTURE_SOURCE_TIMESTAMP", "The source timestamp is in the future")
    elif now - offer.source_ts > age_limit(offer.update_model):
        g4 = _gate("G4", GateStatus.ABSTAIN, "STALE_SOURCE_TIMESTAMP", "Freshness uses source_ts and update_model, not fetched_at")
    elif offer.promo_valid_until is not None and offer.promo_valid_until <= now:
        g4 = _gate("G4", GateStatus.ABSTAIN, "PROMO_EXPIRED", "The promotional price is no longer valid")
    elif offer.promo_starts_at is not None and offer.promo_starts_at > now:
        g4 = _gate("G4", GateStatus.ABSTAIN, "PROMO_NOT_STARTED", "The promotional price has not started")
    elif offer.shipping_status != "known" or offer.shipping is None:
        g4 = _gate("G4", GateStatus.ABSTAIN, "SHIPPING_UNKNOWN", "Shipping is unknown, so the budget cannot be verified")
    elif offer.fees_status != "known" or offer.fees is None:
        g4 = _gate("G4", GateStatus.ABSTAIN, "UNKNOWN_FEES", "Fees are unknown, so the budget cannot be verified")
    else:
        g4 = _gate("G4", GateStatus.PASS, "FRESH_PRICE_AND_SHIPPING", "Source timestamp is fresh and shipping is known")
    return (g1, g2, g3, g4)


def _source_gate(offer: Offer, cohort_sources: int) -> GateResult:
    if cohort_sources >= 2:
        return _gate("G6", GateStatus.PASS, "INDEPENDENT_SOURCES", f"{cohort_sources} independent source IDs corroborate the cohort")
    return _gate("G6", GateStatus.ABSTAIN, "INSUFFICIENT_INDEPENDENT_SOURCES", "Two independent sources are required")


def _integrity_gate(offer: Offer, query: SearchQuery) -> GateResult:
    if not offer.source.approved:
        return _gate("G7", GateStatus.FAIL, "UNAPPROVED_DOMAIN", "Source is not in the approved registry")
    valid_url, url_reason = validate_https_url(offer.url, offer.source.domain)
    if not valid_url:
        return _gate("G7", GateStatus.FAIL, url_reason, "The merchant URL is not a safe approved HTTPS destination")
    if offer.redirect_url:
        redirect_valid, _ = validate_https_url(offer.redirect_url, offer.source.domain)
        if not redirect_valid or normalize_text(offer.redirect_url) != normalize_text(offer.url):
            return _gate("G7", GateStatus.FAIL, "REDIRECT_TRICK", "The source URL redirects to a different or unsafe destination")
    if offer.country != query.country:
        return _gate("G7", GateStatus.FAIL, "COUNTRY_RESTRICTED", "Offer country is outside the request country")
    if offer.availability != "in_stock":
        return _gate("G7", GateStatus.FAIL, "OUT_OF_STOCK", "Offer is not available to buy")
    if offer.condition != "new":
        return _gate("G7", GateStatus.FAIL, "CONDITION_NOT_NEW", "Refurbished and open-box offers are a separate lane")
    if offer.source.breaker_state != "closed":
        return _gate("G7", GateStatus.ABSTAIN, "SOURCE_BREAKER_OPEN", "Source is paused after connector failures")
    return _gate("G7", GateStatus.PASS, "PROVENANCE_RELIABLE", "Approved source, stock, country, and new condition verified")


def _trust_score(offer: Offer, source_count: int, stats: ClusterStats | None, query: SearchQuery) -> int:
    exact_variant = 35
    evidence = 20 if source_count >= 3 else 12
    reliability = max(0, min(15, offer.source.reliability * 15 // 100))
    fresh = 10
    value = 20
    if stats and stats.median_minor is not None and offer.effective_cost() is not None:
        cost = offer.effective_cost().to_minor()
        if cost > stats.median_minor:
            value = max(0, 20 - ((cost - stats.median_minor) * 20 // max(stats.median_minor, 1)))
    return max(0, min(100, exact_variant + evidence + reliability + fresh + value))


def evaluate_offer(
    offer: Offer,
    query: SearchQuery,
    now: datetime,
    selected_cohort: str | None,
    stats: ClusterStats | None,
    source_count: int,
    run_id: str,
    rules_hash: str,
) -> OfferEvaluation:
    gates = list(_initial_gates(offer, query, now))
    if selected_cohort is not None and cohort_key(offer.variant) != selected_cohort and gates[0].status is GateStatus.PASS:
        gates[0] = _gate("G1", GateStatus.FAIL, "DIFFERENT_COHORT", "Only one exact variant cohort can be ranked")
    if stats is None or stats.count < 3:
        g5 = _gate("G5", GateStatus.ABSTAIN, "INSUFFICIENT_ANOMALY_SAMPLE", "At least three comparable prices are needed for MAD")
    else:
        cost = offer.effective_cost()
        z = modified_z(cost.to_minor(), stats) if cost else None
        if z is None:
            g5 = _gate("G5", GateStatus.FAIL, "ANOMALY_MODIFIED_Z", "Price is outside a zero-MAD cluster")
        elif z > MAX_ANOMALY_Z:
            g5 = _gate("G5", GateStatus.FAIL, "ANOMALY_MODIFIED_Z", f"Modified z-score {z} is above {MAX_ANOMALY_Z}")
        else:
            g5 = _gate("G5", GateStatus.PASS, "PRICE_CLUSTERED", "Price is inside the median/MAD peer cluster")
    gates.append(g5)
    gates.append(_source_gate(offer, source_count))
    gates.append(_integrity_gate(offer, query))
    effective = offer.effective_cost()
    score = _trust_score(offer, source_count, stats, query) if effective else None
    stamp = iso_timestamp(offer.source_ts)
    fetched = iso_timestamp(offer.fetched_at)
    # Every numeric fact shown on an Evidence Card carries both timestamps and both hashes.
    evidence_numbers = [EvidenceNumber("price_minor", str(offer.price.to_minor()), stamp, fetched, rules_hash, run_id)]
    if offer.shipping is not None:
        evidence_numbers.append(EvidenceNumber("shipping_minor", str(offer.shipping.to_minor()), stamp, fetched, rules_hash, run_id))
    if effective is not None:
        evidence_numbers.append(EvidenceNumber("effective_cost_minor", str(effective.to_minor()), stamp, fetched, rules_hash, run_id))
    numbers = tuple(evidence_numbers)
    passed = [item for item in gates if item.status is GateStatus.PASS]
    failures = [item for item in gates if item.status is GateStatus.FAIL]
    caveats = [item for item in gates if item.status is GateStatus.ABSTAIN]
    if failures:
        explanation = "Excluded because " + ", ".join(item.reason_code for item in failures) + "."
    elif caveats:
        explanation = "Needs review because " + ", ".join(item.reason_code for item in caveats) + "."
    else:
        explanation = f"Verified peer: {offer.merchant} lists the exact new variant at a known total."
    return OfferEvaluation(offer, tuple(gates), effective, score, numbers, explanation)


def choose_cohort(offers: Iterable[Offer], query: SearchQuery, now: datetime) -> str | None:
    grouped: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        if not variant_matches_query(offer, query) or offer.condition != query.condition:
            continue
        basic = _initial_gates(offer, query, now)
        if all(g.status is GateStatus.PASS for g in basic) and _integrity_gate(offer, query).status is GateStatus.PASS:
            grouped[cohort_key(offer.variant)].append(offer)
    candidates = [
        (cohort, items) for cohort, items in grouped.items()
        if len({item.source.source_id for item in items}) >= 2
    ]
    if not candidates:
        return None
    # This picks the cheapest corroborated cohort; offers are not mixed between cohorts.
    candidates.sort(key=lambda pair: (median([item.effective_cost().to_minor() for item in pair[1] if item.effective_cost()]), pair[0]))
    return candidates[0][0]
