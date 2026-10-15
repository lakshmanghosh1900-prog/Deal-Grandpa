# Deal Grandpa

**Provisional name — trademark not cleared.**

Deal Grandpa is an evidence-first shopping decision tool. A customer writes a plain request such as:

> I need a phone under 30000 with 8GB RAM 128GB storage

Grandpa checks the committed **FIXTURE** dataset, forms an exact-variant comparison cohort, rejects stale or unsafe observations, and explains one simple decision. If the evidence is not sufficient, the product says **No verified recommendation** instead of guessing.

This repository is a reproducible buildathon implementation: Python 3.11.2, FastAPI, Typer, strict Pydantic v2 boundary models, SQLite, and a static HTML/CSS/JavaScript page with no frontend build chain. The deterministic decision core imports no web framework, database driver, or advisor/LLM.

## What is implemented

- CLI and REST API call the same orchestrator.
- Offline **FIXTURE** ingestion with 180 deterministic offers, including exact matches, near-miss storage/network/pack variants, invalid GTIN, stale/future timestamps, a 100x price, duplicate observations, unapproved domain, redirect, country restriction, out-of-stock, expired promotion, unknown shipping, currency mismatch, and condition lanes.
- Identity tiers: GTIN checksum, then brand + MPN, then normalized attributes. An advisor hint is not used to pass a gate; an uncertain hint belongs in `NEEDS_REVIEW`.
- Money is `Decimal` in the domain and integer minor units in SQLite/output storage. Binary floating-point is not used in the core.
- Gates G1–G7 run before a trust score. Cheap does not rescue an incorrect, stale, anomalous, or unverified offer.
- Exact cohort ranking is separate from product selection. One ranking contains one exact new variant across independent merchants; different product cohorts are not mixed into H1 ranking.
- RFC 8785 JSON Canonicalization Scheme (JCS) is used for replay and audit hashes.
- Evidence Cards use a deterministic template. Every displayed price number has `source_ts`, `fetched_at`, `rules_hash`, and `run_id`. `fetched_at` proves collection time only; freshness comes from `source_ts` and `update_model`.
- Source registry health includes breaker state and dead-letter count. Fault injection demonstrates allowlisted retry and quarantine actions.
- Field permissions and retention days are carried by the source registry. `make purge` runs the daily raw-payload deletion workflow while retaining decisions and hashes.

## Quick start

The commands below assume a clean Linux machine with Python 3.11.2 and `uv`. The lockfile is `uv.lock`; dependencies stay inside `.venv`. No credentials are needed for **FIXTURE** mode.

```bash
uv sync --extra test
make test
DEAL_GRANDPA_NOW=2026-10-16T12:00:00Z make run
```

`make test` is the single offline test command. It sets `ADVISOR=none`; the tests make no network calls and complete in under 60 seconds. The `DEAL_GRANDPA_NOW` value makes the committed fixture demonstration reproducible. Without it, source timestamps will eventually become stale and an honest abstention is expected.

Example result (amounts in the human CLI are displayed as currency totals):

```text
FIXTURE mode | run_id: ...
Outcome: RECOMMEND
Grandpa says: Verified recommendation: exact variant, current price, known shipping, and independent evidence passed.
Buy: Shop A — acme grandpa g1 8GB RAM 128GB storage 5g — total ₹25999.00
```

The run is also written to `var/deal_grandpa.sqlite3`. The database is ignored by git.

## Run the interfaces

### CLI

```bash
# Demo request
DEAL_GRANDPA_NOW=2026-10-16T12:00:00Z deal-grandpa demo

# Any plain-language request
deal-grandpa search "I need a phone under 30000 with 8GB RAM 128GB storage"

# Replay without fetching
deal-grandpa replay RUN_ID

# Verify the hash-chained audit log
deal-grandpa verify-log

# Run retention deletion
deal-grandpa purge
```

### REST API and static page

```bash
DEAL_GRANDPA_NOW=2026-10-16T12:00:00Z uvicorn deal_grandpa.api.app:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`. The page is a single static UI labelled **FIXTURE**. It has a query bar, outcome pill (`RECOMMEND`, `WITH_CAVEATS`, or `ABSTAIN`), exact-variant ranked offers, effective-cost breakdown, expanded Evidence Cards with all G1–G7 reason codes, an excluded panel, source health, breaker state, dead-letter count, and a Break-it button.

Endpoints:

- `GET /api/health`
- `POST /api/runs` with `{"query":"..."}`
- `GET /api/runs/{run_id}`
- `POST /api/runs/{run_id}/replay`
- `GET /api/sources`
- `POST /api/fault-injection` with `{"mode":"timeout_once"}`, `persistent_failure`, or `clear`
- `GET /api/verify-log`

No endpoint claims a market-wide best price from one observation; one offer is never presented as market-wide evidence.

## Decision contract

The decision state machine is persisted by `run_id`: `SNAPSHOT → NORMALIZE → GATES → ANOMALY → RANK → EXPLAIN → AUDIT`. Repeating a step is idempotent for a run. The replay bundle contains:

1. immutable source input snapshot and its hash;
2. source-registry snapshot, including permission and retention policy;
3. original query;
4. policy/rules version and `rules_hash`;
5. injected clock;
6. review decisions (empty in this fixture-only build); and
7. final result hash plus hash-chained audit events.

Replay reads the saved input snapshot and never fetches. It returns `MATCH`, `MISMATCH`, or a retention/snapshot reason. Audit verification recomputes every chain link from `GENESIS`.

### Gates before score

| Gate | Meaning | Examples of a non-pass reason |
|---|---|---|
| G1 | Exact product and variant cohort | `VARIANT_MISMATCH`, `DIFFERENT_COHORT` |
| G2 | Identity and input integrity | `INVALID_GTIN`, `IDENTITY_CHECKED` |
| G3 | Exact money, currency, and budget | `CURRENCY_MISMATCH`, `OVER_BUDGET` |
| G4 | Source freshness, promotion window, known shipping | `STALE_SOURCE_TIMESTAMP`, `FUTURE_SOURCE_TIMESTAMP`, `PROMO_EXPIRED`, `SHIPPING_UNKNOWN` |
| G5 | Median/MAD anomaly check | `ANOMALY_MODIFIED_Z`, `INSUFFICIENT_ANOMALY_SAMPLE` |
| G6 | Two independent source IDs | `INSUFFICIENT_INDEPENDENT_SOURCES` |
| G7 | Approved source, stock, country, new condition, breaker | `UNAPPROVED_DOMAIN`, `REDIRECT_TRICK`, `OUT_OF_STOCK`, `SOURCE_BREAKER_OPEN` |

Statuses are exactly `PASS`, `FAIL`, and `ABSTAIN`. `ABSTAIN` never becomes success. A peer must pass every gate, have the same cohort, be new, have reliable price and non-expired promotion data, and be corroborated by at least two independent source IDs. Two offers from one source do not corroborate each other.

Unknown shipping stays in a separate lane and cannot be budget verified. An expired promotion is not applied. `price_minor` already contains discounts; `verified_discount_minor` is evidence metadata only and is never subtracted a second time. A high price produces a warning or anomaly decision; it is not automatically quarantined merely for being high.

### Money and freshness

- Domain money is `Money(Decimal, currency)`. The domain rejects non-Decimal values, negative values, and invalid currency precision.
- SQLite and fixture prices use minor-unit integers: `2599900 INR` means `₹25999.00`.
- Source timestamp rules use `source_ts` plus `update_model`. `fetched_at` is provenance only. A future source timestamp is suspect.
- Currency conversion is not silently performed. A mismatch is excluded or unresolved.

### Anomaly and trust

Comparable prices are deduplicated by source/cohort/cost before the median and MAD calculation. The modified z-score is Decimal arithmetic with factor `0.6745` and limit `3.5`; zero MAD treats equal-to-median values as pass and other values as anomaly.

The trust score is fixed at 0–100 with weights 35 exact variant, 20 evidence, 15 source reliability, 10 freshness, and 20 value. It is calculated only after correctness gates. Ranking sorts verified peers by effective cost first, then trust score for explanation. Equal cost and equal trust has no hidden static tie-breaker and abstains rather than inventing a winner.

## SQLite, permissions, and recovery

The H1 persistence schema has `source_snapshots`, `runs`, `audit_events`, and `source_health`. Raw payloads are immutable from the application path; retention redaction changes only raw payload content to `[REDACTED]` and keeps the hash and run decisions. The registry carries `field_permissions` and `retention_days`; connectors are not allowed to persist fields outside the permission list.

Only these recovery actions are allowed: `retry`, `quarantine`, `expire`, `rollback`, `notify`. The demo uses retry once and then quarantine on persistent failure. No arbitrary recovery action is accepted.

## Tests and golden metrics

`make test` runs unit, golden regression, JCS/reproducibility, schema security, API smoke, replay/audit, and fault-injection tests. The following table is asserted by the CI test suite against `tests/fixtures/golden_offers.json`:

| Metric | Expected |
|---|---:|
| Fixture offers | 180 |
| Verified exact-variant ranked peers | 3 |
| Excluded observations after source/duplicate normalization | 173 |
| Ranked offers with a variant mismatch | 0 |
| Ranked offers missing any G1–G7 PASS | 0 |
| 100x price recommendation | 0 |
| Stale/future observations recommended | 0 |
| One-time connector failure recovery | PASS via `retry` |
| Persistent connector failure | `ABSTAIN`, no recommendation |
| RFC 8785 golden vectors | 2 |
| Network calls in tests | 0 |

Additional direct checks cover decimal addition (`0.10 + 0.20`), strict Pydantic input, invalid GTIN, promo/shipping/currency gates, JCS key order, and hash-chain verification.

## Docker and deployment

Docker installs the pinned runtime and test extra into the image, then starts Uvicorn. The compose file persists only the SQLite data volume:

```bash
docker compose up --build
# visit http://localhost:8000
```

The project does not include credentials or a network source connector. A production connector must implement the source port, pass the same schema and gates, provide source permissions/retention, and preserve raw input snapshots. It must not turn a failed network read into a recommendation.

## Honest limitations

This release is a safe **FIXTURE** demonstration, not a claim of real-time market coverage. It does not scrape retailers, perform currency conversion, infer missing variant attributes, or use an advisor model. The static source is intentionally limited so the recommendation can be replayed offline. SQLite is suitable for a local/small deployment; a multi-worker hosted deployment would need a separately reviewed database adapter and signed/external audit storage.
