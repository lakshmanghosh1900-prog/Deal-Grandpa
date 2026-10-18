# Deal Grandpa

**Provisional name — trademark not cleared.**

Deal Grandpa is an evidence-first shopping decision tool. A customer writes a plain request such as:

> I need a phone under 30000 with 8GB RAM 128GB storage

Grandpa checks the committed **FIXTURE** dataset, forms an exact-variant comparison cohort, rejects stale or unsafe observations, and explains one simple decision. If the evidence is not sufficient, the product says **No verified recommendation** instead of guessing.

This repository is a reproducible buildathon implementation: Python 3.12.11, FastAPI, Typer, strict Pydantic v2 boundary models, SQLite, and a static HTML/CSS/JavaScript page with no frontend build chain. The deterministic decision core imports no web framework, database driver, or advisor/LLM.

## What is implemented

- CLI and REST API call the same orchestrator.
- Offline **FIXTURE** ingestion with 180 deterministic offers, including exact matches, near-miss storage/network/pack variants, invalid GTIN, stale/future timestamps, a 100x price, duplicate observations, unapproved domain, redirect, country restriction, out-of-stock, expired promotion, unknown shipping, currency mismatch, and condition lanes.
- Identity tiers: GTIN checksum, then brand + MPN, then normalized attributes. An advisor hint is not used to pass a gate; an uncertain hint belongs in `NEEDS_REVIEW`.
- Money is `Decimal` in the domain and integer minor units in SQLite/output storage. Binary floating-point is not used in the core.
- Gates G1–G7 run before a trust score. Cheap does not rescue an incorrect, stale, anomalous, or unverified offer.
- Exact cohort ranking is separate from product selection. One ranking contains one exact new variant across independent merchants; different product cohorts are not mixed into H1 ranking.
- RFC 8785 JSON Canonicalization Scheme (JCS) is used for replay and audit hashes.
- Evidence Cards use a deterministic template. Every displayed price number has `source_ts`, `fetched_at`, `rules_hash`, and `run_id`. `fetched_at` proves collection time only; freshness comes from `source_ts` and `update_model`.
- Source registry health includes breaker state and dead-letter count. Developer-only fault injection demonstrates allowlisted retry and quarantine actions, but is disabled by default and must remain disabled on internet-facing deployments.
- Field permissions and explicit revoked fields are enforced in G1. Denied offers are excluded with `PERMISSION_DENIED` or `PERMISSION_REVOKED`; restricted values are not normalized into snapshots, result cards, or audit payloads. `make purge` runs the daily raw-payload deletion workflow while retaining decisions and hashes.
- Merchant destinations must be HTTPS on the exact approved hostname, with no userinfo or non-443 port. Redirect targets are independently checked. Fixture URLs remain non-clickable and are never rendered as anchors.

### Fixture accounting

The committed fixture contains **180 raw observations**. The existing condition-aware
deduplication step retains **178 observations**: the only duplicate fingerprint group
is `offer-0001`, `offer-0011`, and `offer-0012`; `offer-0001` is retained and the
other two observations are removed. The fixed-clock demo then ranks **3 verified
offers** and reports **175 excluded evaluations** (`178 = 3 + 175`). Ranked and
excluded counts therefore describe post-deduplication observations, not all 180 raw
records. The fixture records are not deleted to manufacture these totals.

## Quick start

The commands below assume a clean Linux machine with Python 3.12.11 and `uv`. The lockfile is `uv.lock`; dependencies stay inside `.venv`. No credentials are needed for **FIXTURE** mode.

```bash
uv sync --locked --extra test
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

Open `http://localhost:8000`. The page is a single static UI labelled **FIXTURE**. It has a query bar, outcome pill (`RECOMMEND`, `WITH_CAVEATS`, or `ABSTAIN`), exact-variant ranked offers, effective-cost breakdown, expanded Evidence Cards with all G1–G7 reason codes, an excluded panel and source health. Developer-only fault controls are hidden by default and appear only when explicitly enabled.

Endpoints:

- `GET /api/health`
- `GET /api/config` (reports whether developer fault injection is enabled)
- `POST /api/runs` with `{"query":"..."}`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/evidence`
- `POST /api/runs/{run_id}/replay`
- `GET /api/sources`
- `POST /api/fault-injection` with `{"mode":"timeout_once"}`, `persistent_failure`, or `clear` (disabled unless `DEAL_GRANDPA_ENABLE_FAULT_INJECTION=true`; local development only)
- `GET /api/verify-log`

No endpoint claims a market-wide best price from one observation; one offer is never presented as market-wide evidence.

### Source-adapter boundary

`src/deal_grandpa/adapters/contracts.py` defines the minimal `SourceAdapter`
contract. An approved adapter must return a validated `LoadedFixture` containing
normalized offers and `SourceInfo` registry metadata. The fixture adapter is the
only configured source in this stage. Schema errors and source failures are not
turned into prices; a failed injected adapter retries/quarantines and produces an
abstention without falling back to fixture data. Future live connectors require a
separately approved source, permissions, and network scope.

## Decision contract

The decision state machine is persisted by `run_id`: `SNAPSHOT → NORMALIZE → GATES → ANOMALY → RANK → EXPLAIN → AUDIT`. Repeating a step is idempotent for a run. Replay reconstructs the normalized fixture from the saved snapshot and does not read the current fixture path. It verifies the stored policy version, review version, rules hash, payload hash, and registry snapshot before evaluating. The replay bundle contains:

1. immutable source input snapshot and its hash;
2. source-registry snapshot, including permission and retention policy;
3. original query;
4. policy/rules version and `rules_hash`;
5. injected clock;
6. review decisions (empty in this fixture-only build); and
7. final result hash plus hash-chained audit events; and
8. an external checkpoint at `var/audit_checkpoint.json` containing the expected chain count and terminal hash.

Replay reads the saved input snapshot and never fetches. It returns `MATCH`, `MISMATCH`, or a retention/snapshot reason. Audit verification recomputes every chain link from `GENESIS`.

### Gates before score

| Gate | Meaning | Examples of a non-pass reason |
|---|---|---|
| G1 | Permissions, exact product and variant cohort | `PERMISSION_DENIED`, `PERMISSION_REVOKED`, `VARIANT_MISMATCH`, `CONDITION_LANE_MISMATCH`, `DIFFERENT_COHORT` |
| G2 | Identity and input integrity | `INVALID_GTIN`, `IDENTITY_CHECKED` |
| G3 | Exact money, currency, and budget | `CURRENCY_MISMATCH`, `OVER_BUDGET` |
| G4 | Source freshness, promotion window, known shipping and fees | `STALE_SOURCE_TIMESTAMP`, `FUTURE_SOURCE_TIMESTAMP`, `PROMO_EXPIRED`, `SHIPPING_UNKNOWN`, `UNKNOWN_FEES` |
| G5 | Median/MAD anomaly check | `ANOMALY_MODIFIED_Z`, `INSUFFICIENT_ANOMALY_SAMPLE` |
| G6 | Two independent source IDs | `INSUFFICIENT_INDEPENDENT_SOURCES` |
| G7 | Approved source, exact HTTPS destination, stock, country, new condition, breaker | `UNAPPROVED_DOMAIN`, `INVALID_MERCHANT_URL`, `URL_DOMAIN_MISMATCH`, `REDIRECT_TRICK`, `OUT_OF_STOCK`, `SOURCE_BREAKER_OPEN` |

Statuses are exactly `PASS`, `FAIL`, and `ABSTAIN`. `ABSTAIN` never becomes success. A peer must pass every gate, have the same cohort, be new, have reliable price and non-expired promotion data, and be corroborated by at least two independent source IDs. Two offers from one source do not corroborate each other.

Unknown shipping or fees stays in a separate lane with `UNKNOWN_FEES` and cannot be budget verified. An expired promotion is not applied. `price_minor` already contains discounts; `verified_discount_minor` is evidence metadata only and is never subtracted a second time. The CLI, API evidence response, and UI expose item price, shipping, known fees, verified discount metadata, and effective total. A high price produces a warning or anomaly decision; it is not automatically quarantined merely for being high.

### Money and freshness

- Domain money is `Money(Decimal, currency)`. The domain rejects non-Decimal values, negative values, and invalid currency precision.
- SQLite and fixture prices use minor-unit integers: `2599900 INR` means `₹25999.00`.
- Source timestamp rules use `source_ts` plus `update_model`. `fetched_at` is provenance only. A future source timestamp is suspect.
- Currency conversion is not silently performed. A mismatch is excluded or unresolved.

### Anomaly and trust

Comparable prices are deduplicated by source/cohort/cost before the median and MAD calculation. The modified z-score is Decimal arithmetic with factor `0.6745` and limit `3.5`; zero MAD treats equal-to-median values as pass and other values as anomaly.

The trust score is fixed at 0–100 with weights 35 exact variant, 20 evidence, 15 source reliability, 10 freshness, and 20 value. It is calculated only after correctness gates. Ranking sorts verified peers by effective cost first, then trust score for explanation. Equal cost and equal trust has no hidden static tie-breaker and abstains rather than inventing a winner.

## SQLite, permissions, and recovery

The H1 persistence schema has `source_snapshots`, `runs`, `audit_events`, and `source_health`. Snapshots contain a permission-filtered projection; restricted raw values are not persisted. Retention redaction changes only raw payload content to `[REDACTED]` and keeps the hash and run decisions. The registry carries `field_permissions`, `revoked_fields`, and `retention_days`; connectors are not allowed to persist fields outside the permission list. The external checkpoint detects tail deletion and head mismatch; it is an integrity anchor, not a complete tamper-proof system if both database and checkpoint are compromised.

Only these recovery actions are allowed: `retry`, `quarantine`, `expire`, `rollback`, `notify`. The demo uses retry once and then quarantine on persistent failure. No arbitrary recovery action is accepted.

## Tests and golden metrics

`make test` runs 47 unit/integration tests covering golden regression, JCS/reproducibility, strict schema security, permissions, condition lanes, URL safety, API smoke, replay/audit, retention, cost breakdown, source-adapter contracts, and fault injection. The following table is asserted by the CI test suite against `tests/fixtures/golden_offers.json`:

| Metric | Expected |
|---|---:|
| Raw fixture observations | 180 |
| After `_deduplicate` | 178 |
| Duplicate fingerprint group | `offer-0001`, `offer-0011`, `offer-0012` |
| Verified exact-variant ranked peers | 3 |
| Excluded observations after source/condition normalization | 175 |
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

# Build, start, and run tests in a clean image
make docker-test
```

The project does not include credentials or a network source connector. A production connector must implement the source port, pass the same schema and gates, provide source permissions/retention, and preserve raw input snapshots. It must not turn a failed network read into a recommendation.

## Honest limitations

This release is a safe **FIXTURE** prototype, not a production-ready claim or a claim of market coverage. It does not scrape retailers, perform currency conversion, infer missing attributes, or use an advisor model. The static source is intentionally limited so the recommendation can be replayed offline. Developer fault injection is disabled by default. The API does not yet include authentication or request rate limiting; do not expose it to the public internet until a reviewed access-control and rate-limit boundary is added. SQLite is suitable for a local/small deployment; a multi-worker hosted deployment would need a separately reviewed database adapter and stronger external audit controls. If Docker is unavailable, `make docker-test` must be reported as blocked rather than assumed to pass.
