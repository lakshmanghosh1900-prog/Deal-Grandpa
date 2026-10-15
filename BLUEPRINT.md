# Deal Grandpa blueprint

**Provisional name; trademark not cleared.** This blueprint records the implementation contract for the current repository. The safe default is to abstain. A low price is never permission to overlook identity, condition, source, timestamp, currency, shipping, promotion, or provenance.

## 1. User outcome

A customer enters a plain-language shopping request. Deal Grandpa turns it into strict constraints, evaluates source observations, compares only exact new variants, and explains the result as a human would:

- **Verified** — all required gates pass and independent evidence is sufficient.
- **Verified with caveats** — evidence is useful but a non-fatal caveat remains.
- **Needs review** — source or attribute evidence is incomplete.
- **Excluded** — a deterministic rule rejected the offer.
- **No verified recommendation** — there is no honest winner.

The UI outcome pills are `RECOMMEND`, `WITH_CAVEATS`, and `ABSTAIN`. They are not confidence theater. A recommendation object is `null` for every abstention.

The first supported request is a phone with a budget, RAM, and storage constraint. Query parsing is deliberately conservative: unknown attributes are not invented.

## 2. Hexagonal architecture

```text
Typer CLI ─┐
FastAPI   ─┼─> DealGrandpaService / state machine ─> pure domain policy
Static UI ─┘                         │
                       ┌────────────┴────────────┐
                 Fixture source              SQLite store
                  (adapter)                   (adapter)
```

The core under `src/deal_grandpa/domain/` imports only the Python standard library. It contains money, variant identity, gates, anomaly arithmetic, cohort selection, and deterministic explanations. It does not import FastAPI, Typer, SQLite, web clients, environment settings, or an advisor model.

The current file map is:

```text
src/deal_grandpa/
  domain/models.py        immutable Money, Offer, Query, Decision, gate types
  domain/policy.py        normalization, query parsing, identity, G1–G7, MAD, trust
  domain/canonical.py     RFC 8785 JCS bytes and SHA-256
  schemas.py              strict Pydantic v2 boundary models
  adapters/fixture_source.py  JSON fixture connector and normalization adapter
  adapters/sqlite.py      SQLite schema, snapshots, runs, audit chain, retention
  orchestrator.py         idempotent run state machine, replay, evidence cards
  cli.py                  Typer commands
  api/app.py              FastAPI routes and static-file adapter
  static/                 one no-build-chain browser page
tests/                    unit, integration, security, golden and fault checks
```

## 3. Exact comparison contract

### Cohort key

A cohort is the normalized tuple:

```text
category | brand | model | ram_gb | storage_gb | network | pack_count | color
```

Normalization trims, collapses whitespace, and uses Unicode case folding. Requested fields must match exactly. A missing requested attribute is not inferred. `128GB` and `256GB`, `4G` and `5G`, and pack 1 and pack 2 are never one cohort. Condition is outside the key but is a hard peer rule: `new`, `refurbished`, and `open_box` are separate lanes.

The system clusters first. It selects one corroborated cohort for the request and ranks only offers in that cohort. Different products are not mixed into H1 ranking. Product-level recommendation selection remains separate from merchant ranking; if the system cannot establish a single verified cohort, it returns no recommendation.

### Identity tiers

1. Tier 1: validate GTIN length, digits, and checksum when a GTIN is supplied.
2. Tier 2: use normalized brand + MPN, with brand + model as the fixture fallback.
3. Tier 3: compare the requested and observed normalized attributes.
4. Tier 4: an advisor/AI hint can only create `NEEDS_REVIEW`; it cannot pass an identity gate.

Two offers from one source cannot corroborate each other. Retries, mirror URLs, and source-owned record IDs do not create independent evidence. Source identity is the registry `source_id`.

## 4. Gate policy

Gates are evaluated before trust scoring and before ranking. Each card always contains G1 through G7 with `PASS`, `FAIL`, or `ABSTAIN`, a stable reason code, and a human detail.

| Gate | Pass condition | Typical reject or abstain codes |
|---|---|---|
| G1 exact variant | Query fields and selected cohort match; condition is new | `VARIANT_MISMATCH`, `DIFFERENT_COHORT` |
| G2 identity | GTIN checksum valid when present; fallback identity exists | `INVALID_GTIN` |
| G3 money | Decimal/minor-unit price, same currency, known total under budget | `CURRENCY_MISMATCH`, `OVER_BUDGET` |
| G4 freshness | `source_ts` is not future or stale; promo active; shipping known | `STALE_SOURCE_TIMESTAMP`, `FUTURE_SOURCE_TIMESTAMP`, `PROMO_EXPIRED`, `PROMO_NOT_STARTED`, `SHIPPING_UNKNOWN` |
| G5 anomaly | effective cost belongs to median/MAD peer cluster | `ANOMALY_MODIFIED_Z`, `INSUFFICIENT_ANOMALY_SAMPLE` |
| G6 evidence | at least two distinct approved source IDs in the cohort | `INSUFFICIENT_INDEPENDENT_SOURCES` |
| G7 trust boundary | approved domain, URL, country, stock, new condition, closed breaker | `UNAPPROVED_DOMAIN`, `REDIRECT_TRICK`, `COUNTRY_RESTRICTED`, `OUT_OF_STOCK`, `SOURCE_BREAKER_OPEN` |

A gate failure excludes the offer from the verified lane. An abstention is not silently turned into failure or success. Unknown shipping has no effective cost and cannot be budget-verified. A promotion where `promo_valid_until <= now` is expired; its promotional price is rejected. `price_minor` already includes discounts. `verified_discount_minor` is a separately proven fact and is never subtracted again.

## 5. Exact money and time rules

`Money` contains a `Decimal` and a three-letter currency. It rejects non-finite, negative, or over-precision values. Domain arithmetic never constructs or accepts binary floating-point numbers. Persistence uses integer minor units; the currency scale is explicit (`INR`, `USD`, `EUR`, `GBP` use two; `JPY` uses zero). Currency conversion is out of scope and never silent.

Freshness is calculated from `source_ts` and the source `update_model`:

- hourly: six-hour acceptance window;
- daily: one day;
- weekly: seven days;
- `push_realtime`: fifteen minutes.

`fetched_at` is kept on every evidence number but cannot make a stale source current. A future `source_ts` is `ABSTAIN`.

## 6. Anomaly and trust

The anomaly input is the effective total (`price_minor + known shipping_minor`) for deduplicated source/cohort/cost observations. The implementation uses exact integer minor units and Decimal z-score arithmetic:

```text
median = middle sorted effective costs
MAD = median(abs(cost - median))
modified_z = 0.6745 * abs(cost - median) / MAD
reject when modified_z > 3.5
```

With zero MAD, equal-to-median passes and every other value is anomalous. High price alone is a warning/anomaly input; it is not an automatic quarantine outside this deterministic rule.

The trust score is fixed at 0–100 after gates:

```text
35 exact variant + 20 evidence + 15 source reliability + 10 freshness + 20 value
```

Ranking is effective cost first, then trust score. A tie in both has no static tie-breaker. The service abstains instead of hiding a choice behind merchant name or offer ID. Offer IDs may make presentation order reproducible, but they cannot decide a tied recommendation.

## 7. Evidence Card contract

The deterministic `evidence-card-v1` template contains:

- offer ID and merchant;
- exact normalized variant;
- price, shipping, and derived effective cost;
- fixed trust score or `null`;
- all G1–G7 results and reason codes;
- plain-language explanation; and
- a provenance object for every numeric fact: `source_ts`, `fetched_at`, `rules_hash`, `run_id`.

An advisor rewrite is not used in this build. If added later, it must be passed through a number-preservation validator against the card before it can reach the user. The template is intentionally deterministic so a reviewer can reconstruct the result.

## 8. Replay, persistence, and audit

Pydantic v2 strict models reject unknown fields and numeric strings at the input boundary. SQLite tables are:

- `source_snapshots`: immutable raw fixture payload hash, capture time, registry snapshot, and retention-redaction marker;
- `runs`: run ID, query, policy/rules hash, injected clock, snapshot ID, registry snapshot, review decisions, state, result, result hash;
- `audit_events`: append-only hash chain with event type, actor, previous hash, and event hash;
- `source_health`: breaker state, dead-letter count, and update time.

Replay inputs are complete: immutable source snapshot, source-registry snapshot, query, policy version, review decisions, and clock. The canonical replay bundle is hashed with RFC 8785 JCS, never a hand-written sorted JSON function. The two committed JCS vectors are tested, including Unicode and number normalization.

The run state machine is persisted after each idempotent step:

```text
SNAPSHOT → NORMALIZE → GATES → ANOMALY → RANK → EXPLAIN → AUDIT
```

The source registry also carries field-level permission names and `retention_days`. A scheduled `purge` redacts expired raw payloads, retains provenance hashes and decisions, and reports the number of redactions.

## 9. Source health and recovery

The fixture connector has no network dependency. The Break-it UI control injects a timeout once or a persistent failure. Recovery can use only:

```text
retry, quarantine, expire, rollback, notify
```

A one-time timeout retries idempotently and produces the same verified result. A persistent failure is quarantined and results in `ABSTAIN`; it never becomes a guessed price. Breaker state and dead-letter count remain visible in the UI and API.

## 10. Golden dataset and quality gates

`tests/fixtures/golden_offers.json` contains 180 offers and five registry entries. The default fixed-clock run has three verified ranked peers and 173 excluded observations. The test suite asserts:

- exact variant only in the ranked lane;
- near-miss 128/256, 5G/4G, and pack 1/2 rejection;
- invalid GTIN, stale, future, 100x, duplicate, unapproved, redirect, country, stock, promotion, shipping, and currency handling;
- no source may corroborate itself;
- Decimal money arithmetic and strict Pydantic;
- median/MAD including zero MAD;
- JCS golden hashes and hash-chain verification;
- fault retry, quarantine, and honest abstention;
- API health and static serving; and
- an offline `make test` under the test budget.

## 11. Delivery and honest boundary

`Dockerfile`, `docker-compose.yml`, `.python-version`, `uv.lock`, `.env.example`, Makefile, README, and tests are committed. The image installs all runtime and test dependencies in the project image; no global dependency or credential is required.

The current adapter is intentionally fixture-only. It does not claim retailer coverage, currency conversion, missing-attribute inference, or advisor accuracy. A future connector must preserve the same schemas, permission checks, snapshots, provenance, gates, and replay behavior before it can be used for customer decisions.
