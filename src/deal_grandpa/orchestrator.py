from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from deal_grandpa.adapters.fixture_source import FixtureSource, LoadedFixture, SourceError
from deal_grandpa.adapters.contracts import SourceAdapter, SourceAdapterError
from deal_grandpa.adapters.sqlite import SQLiteStore
from dataclasses import replace

from deal_grandpa.domain.canonical import canonical_hash
from deal_grandpa.domain.models import (
    ClusterStats,
    Decision,
    EvidenceNumber,
    GateStatus,
    Offer,
    OfferEvaluation,
    Outcome,
    SearchQuery,
    format_money,
    SourceInfo,
)
from deal_grandpa.domain.policy import (
    REVIEW_VERSION,
    RULESET_VERSION,
    _integrity_gate,
    _initial_gates,
    choose_cohort,
    cluster_statistics,
    cohort_key,
    evaluate_offer,
    iso_timestamp,
    parse_query,
    parse_timestamp,
    policy_manifest,
    variant_matches_query,
)


class DealGrandpaService:
    """State-machine orchestrator. Each step is persisted and safe to repeat."""

    def __init__(
        self,
        store: SQLiteStore,
        fixture_path: str,
        clock: Callable[[], datetime] | None = None,
        source_adapter: SourceAdapter | None = None,
    ):
        self.store = store
        self.fixture_path = fixture_path
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.source_adapter = source_adapter
        self.fault_mode: str | None = None

    @staticmethod
    def _registry_dict(sources: tuple[SourceInfo, ...]) -> list[dict[str, Any]]:
        return [
            {
                "source_id": source.source_id,
                "domain": source.domain,
                "approved": source.approved,
                "reliability": source.reliability,
                "retention_days": source.retention_days,
                "breaker_state": source.breaker_state,
                "dead_letter_count": source.dead_letter_count,
                "field_permissions": list(source.field_permissions),
                "revoked_fields": list(source.revoked_fields),
            }
            for source in sources
        ]

    @staticmethod
    def _policy_metadata() -> tuple[str, str, str]:
        manifest = policy_manifest()
        return RULESET_VERSION, REVIEW_VERSION, canonical_hash(manifest)

    @staticmethod
    def _deduplicate(offers: tuple[Offer, ...]) -> tuple[Offer, ...]:
        by_fingerprint: dict[tuple[str, str, str, int, int | None], Offer] = {}
        for offer in offers:
            cost = offer.effective_cost()
            key = (
                offer.source.source_id,
                cohort_key(offer.variant),
                offer.condition,
                offer.price.to_minor(),
                cost.to_minor() if cost else None,
            )
            current = by_fingerprint.get(key)
            if current is None or offer.offer_id < current.offer_id:
                by_fingerprint[key] = offer
        return tuple(sorted(by_fingerprint.values(), key=lambda item: item.offer_id))

    def _load_with_recovery(self, run_id: str, actor: str) -> tuple[LoadedFixture, tuple[str, ...]]:
        source = self.source_adapter or FixtureSource(self.fixture_path)
        recovered: list[str] = []
        try:
            loaded = source.load(self.fault_mode)
        except SourceAdapterError as error:
            self.store.append_audit(str(uuid.uuid4()), run_id, "SOURCE_RETRY", actor, {"action": "retry", "error": str(error)})
            recovered.append("retry")
            try:
                loaded = source.load(self.fault_mode)
            except SourceAdapterError as persistent:
                self.store.append_audit(str(uuid.uuid4()), run_id, "SOURCE_QUARANTINE", actor, {"action": "quarantine", "error": str(persistent)})
                recovered.append("quarantine")
                if self.source_adapter is not None:
                    empty = {"fixture_id": f"quarantined-{run_id}", "sources": [], "offers": []}
                    return LoadedFixture(f"quarantined-{run_id}", empty, tuple(), tuple()), tuple(recovered)
                # The fixture adapter has an immutable local input to preserve;
                # an injected future source must never fall back to fixture prices.
                loaded = FixtureSource(self.fixture_path).load(None)
                return LoadedFixture(loaded.fixture_id, loaded.raw_payload, loaded.sources, tuple()), tuple(recovered)
        return loaded, tuple(recovered)

    def _evaluate_loaded_fixture(
        self,
        query_text: str,
        run_id: str,
        current: datetime,
        loaded: LoadedFixture,
        recovery: tuple[str, ...],
        actor: str,
        persist: bool,
    ) -> Decision:
        query = parse_query(query_text)
        policy_version, review_version, rules_hash = self._policy_metadata()
        snapshot_id = canonical_hash(loaded.raw_payload)
        registry = self._registry_dict(loaded.sources)
        if persist:
            self.store.save_snapshot(snapshot_id, loaded.fixture_id, loaded.raw_payload, registry, iso_timestamp(current))
            self.store.start_run(run_id, snapshot_id, query_text, policy_version, rules_hash, review_version, iso_timestamp(current), registry, [])
            self.store.append_audit(
                str(uuid.uuid4()), run_id, "RUN_STARTED", actor,
                {"query": query_text, "snapshot_id": snapshot_id, "policy_version": policy_version, "rules_hash": rules_hash, "review_version": review_version},
            )
            for source in loaded.sources:
                self.store.upsert_source_health(source.source_id, source.breaker_state, source.dead_letter_count)
            self.store.update_state(run_id, "NORMALIZE")
        offers = self._deduplicate(loaded.offers)
        if persist:
            self.store.update_state(run_id, "GATES")
        selected = choose_cohort(offers, query, current)
        stat_offers = tuple(
            offer for offer in offers
            if variant_matches_query(offer, query)
            and offer.condition == query.condition
            and all(g.status is GateStatus.PASS for g in _initial_gates(offer, query, current))
            and _integrity_gate(offer, query).status is GateStatus.PASS
        )
        all_stats = cluster_statistics(stat_offers)
        if persist:
            self.store.update_state(run_id, "ANOMALY")
        source_sets: dict[tuple[str, str], set[str]] = defaultdict(set)
        for offer in stat_offers:
            source_sets[(cohort_key(offer.variant), offer.condition)].add(offer.source.source_id)
        cohort_sources = {key: len(value) for key, value in source_sets.items()}
        evaluations = tuple(
            evaluate_offer(
                offer, query, current, selected,
                all_stats.get((cohort_key(offer.variant), offer.condition)),
                cohort_sources.get((cohort_key(offer.variant), offer.condition), 0),
                run_id, rules_hash,
            )
            for offer in offers
        )
        if persist:
            self.store.update_state(run_id, "RANK")
        peer_winners: dict[str, OfferEvaluation] = {}
        if selected:
            for item in evaluations:
                if not item.eligible or cohort_key(item.offer.variant) != selected:
                    continue
                source_id = item.offer.source.source_id
                previous = peer_winners.get(source_id)
                if previous is None or (item.effective_cost.to_minor(), item.offer.offer_id) < (previous.effective_cost.to_minor(), previous.offer.offer_id):
                    peer_winners[source_id] = item
        ranked = tuple(sorted(peer_winners.values(), key=lambda item: (item.effective_cost.to_minor(), -(item.trust_score or 0), item.offer.offer_id)))
        ranked_ids = {item.offer.offer_id for item in ranked}
        excluded = tuple(
            replace(item, explanation="Excluded because SAME_SOURCE_ALTERNATE.")
            if item.eligible and item.offer.offer_id not in ranked_ids and selected and cohort_key(item.offer.variant) == selected
            else item
            for item in evaluations if item.offer.offer_id not in ranked_ids
        )
        if persist:
            self.store.update_state(run_id, "EXPLAIN")
        recommendation: OfferEvaluation | None = None
        reasons: list[str] = []
        if not selected:
            reasons.append("No exact variant cohort had two independent approved sources.")
        elif not ranked:
            reasons.append("No verified recommendation: every exact-variant peer has a failed or unresolved gate.")
        elif len(ranked) > 1 and ranked[0].effective_cost.to_minor() == ranked[1].effective_cost.to_minor() and ranked[0].trust_score == ranked[1].trust_score:
            reasons.append("No verified recommendation: the top verified offers are tied without a hidden tie-breaker.")
        else:
            recommendation = ranked[0]
            reasons.append("Verified recommendation: exact variant, current price, known shipping, and independent evidence passed.")
        if recovery:
            reasons.append("Connector recovered with allowlisted action(s): " + ", ".join(recovery) + ".")
        outcome = Outcome.RECOMMEND if recommendation else Outcome.ABSTAIN
        decision = Decision(
            run_id=run_id, query=query, outcome=outcome, selected_cohort=selected,
            ranked=ranked, excluded=excluded,
            cluster_stats=tuple(sorted(all_stats.values(), key=lambda item: (item.cohort, item.condition))),
            recommendation=recommendation, reasons=tuple(reasons), rules_hash=rules_hash,
            policy_version=policy_version, review_version=review_version,
            source_registry_snapshot=loaded.sources,
        )
        if persist:
            self.store.update_state(run_id, "AUDIT")
            result = decision_to_dict(decision)
            result["recovery"] = list(recovery)
            result_hash = canonical_hash(result)
            self.store.finish_run(run_id, outcome.value, result, result_hash)
            self.store.append_audit(str(uuid.uuid4()), run_id, "RUN_FINISHED", actor, {"outcome": outcome.value, "result_hash": result_hash})
        return decision

    def run(
        self,
        query_text: str,
        run_id: str | None = None,
        now: datetime | None = None,
        actor: str = "cli",
    ) -> Decision:
        run_id = run_id or str(uuid.uuid4())
        current = (now or self.clock()).astimezone(timezone.utc)
        loaded, recovery = self._load_with_recovery(run_id, actor)
        return self._evaluate_loaded_fixture(query_text, run_id, current, loaded, recovery, actor, persist=True)

    def replay(self, run_id: str, actor: str = "cli") -> dict[str, Any]:
        row = self.store.run(run_id)
        if row is None:
            raise KeyError(f"unknown run_id {run_id}")
        snapshot = self.store.snapshot(row["snapshot_id"])
        if snapshot is None or snapshot["payload_json"] == "[REDACTED]":
            return {"run_id": run_id, "status": "MISMATCH", "reason": "immutable input snapshot was redacted by retention policy"}
        policy_version, review_version, rules_hash = self._policy_metadata()
        if row["policy_version"] != policy_version or row["rules_hash"] != rules_hash:
            return {"run_id": run_id, "status": "MISMATCH", "reason": "POLICY_VERSION_MISMATCH"}
        if row["review_version"] != review_version:
            return {"run_id": run_id, "status": "MISMATCH", "reason": "REVIEW_VERSION_MISMATCH"}
        try:
            raw = json.loads(snapshot["payload_json"])
        except json.JSONDecodeError:
            return {"run_id": run_id, "status": "MISMATCH", "reason": "SNAPSHOT_INVALID"}
        if canonical_hash(raw) != snapshot["payload_hash"] or snapshot["snapshot_id"] != canonical_hash(raw):
            return {"run_id": run_id, "status": "MISMATCH", "reason": "SNAPSHOT_HASH_MISMATCH"}
        loaded = FixtureSource.load_payload(raw)
        stored_registry = json.loads(snapshot["source_registry_json"])
        if canonical_hash(stored_registry) != canonical_hash(self._registry_dict(loaded.sources)):
            return {"run_id": run_id, "status": "MISMATCH", "reason": "REGISTRY_SNAPSHOT_MISMATCH"}
        stored_result = json.loads(row["result_json"] or "{}")
        recovery = tuple(stored_result.get("recovery", []))
        if "quarantine" in recovery:
            loaded = replace(loaded, offers=tuple())
        replayed = self._evaluate_loaded_fixture(
            row["query_text"], run_id, parse_timestamp(row["clock"]), loaded, recovery, actor, persist=False,
        )
        result = decision_to_dict(replayed)
        result["recovery"] = list(recovery)
        actual = canonical_hash(result)
        status = "MATCH" if actual == row["result_hash"] else "MISMATCH"
        self.store.append_audit(str(uuid.uuid4()), run_id, "REPLAY_VERIFIED", actor, {"status": status, "expected": row["result_hash"], "actual": actual})
        return {"run_id": run_id, "status": status, "expected": row["result_hash"], "actual": actual}

    def verify_log(self) -> tuple[bool, str]:
        return self.store.verify_audit()

    def purge(self) -> int:
        return self.store.purge_expired_raw()


def gate_to_dict(gate: Any) -> dict[str, str]:
    return {"gate": gate.gate, "status": gate.status.value, "reason_code": gate.reason_code, "detail": gate.detail}


def evidence_card(item: OfferEvaluation) -> dict[str, Any]:
    restricted = bool(item.offer.permission_failures)
    shipping_minor = item.offer.shipping.to_minor() if item.offer.shipping and not restricted else None
    fees_minor = item.offer.fees.to_minor() if item.offer.fees and not restricted else None
    effective_minor = item.effective_cost.to_minor() if item.effective_cost and not restricted else None
    return {
        "template": "evidence-card-v1",
        "offer_id": item.offer.offer_id,
        "merchant": None if restricted else item.offer.merchant,
        "variant": None if restricted else item.offer.variant.label(),
        "price_minor": None if restricted else item.offer.price.to_minor(),
        "shipping_minor": shipping_minor,
        "effective_cost_minor": effective_minor,
        "item_price_minor": None if restricted else item.offer.price.to_minor(),
        "shipping_cost_minor": shipping_minor,
        "verified_discount_minor": None if restricted or item.offer.verified_discount is None else item.offer.verified_discount.to_minor(),
        "known_fees_minor": [] if restricted or item.offer.fees is None else [fees_minor],
        "fees_status": "UNKNOWN_FEES" if restricted or item.offer.effective_cost() is None else "KNOWN",
        "effective_total_minor": effective_minor,
        "currency": None if restricted else item.offer.price.currency,
        "price_display": None if restricted else format_money(item.offer.price),
        "shipping_display": None if restricted else format_money(item.offer.shipping),
        "fees_display": None if restricted else format_money(item.offer.fees),
        "effective_cost_display": None if restricted else format_money(item.effective_cost),
        "trust_score": item.trust_score,
        "gates": [gate_to_dict(gate) for gate in item.gates],
        "numbers": [
            {"name": number.name, "value": number.value, "source_ts": number.source_ts, "fetched_at": number.fetched_at, "rules_hash": number.rules_hash, "run_id": number.run_id}
            for number in item.evidence_numbers
        ],
        "explanation": item.explanation,
    }


def decision_to_dict(decision: Decision) -> dict[str, Any]:
    return {
        "run_id": decision.run_id,
        "outcome": decision.outcome.value,
        "query": decision.query.raw,
        "selected_cohort": decision.selected_cohort,
        "recommendation": evidence_card(decision.recommendation) if decision.recommendation else None,
        "ranked": [evidence_card(item) for item in decision.ranked],
        "excluded": [evidence_card(item) for item in decision.excluded],
        "cluster_stats": [
            {"cohort": stat.cohort, "condition": stat.condition, "count": stat.count, "median_minor": stat.median_minor, "mad_minor": stat.mad_minor}
            for stat in decision.cluster_stats
        ],
        "reasons": list(decision.reasons),
        "policy_version": decision.policy_version,
        "review_version": decision.review_version,
        "rules_hash": decision.rules_hash,
        "source_health": [
            {"source_id": source.source_id, "domain": source.domain, "breaker_state": source.breaker_state, "dead_letter_count": source.dead_letter_count, "reliability": source.reliability}
            for source in decision.source_registry_snapshot
        ],
    }
