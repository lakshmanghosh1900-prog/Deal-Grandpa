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

The current fixture orchestrator emits `RECOMMEND` or `ABSTAIN`; `WITH_CAVEATS` remains a reserved contract value and is not an active result path until its non-fatal caveat semantics are separately specified and tested.

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
  adapters/contracts.py       SourceAdapter contract for future approved sources
  adapters/sqlite.py      SQLite schema, snapshots, runs, audit chain, retention
  orchestrator.py         idempotent run state machine, replay, evidence cards
  cli.py                  Typer commands
  api/app.py              FastAPI routes and static-file adapter
  static/                 one no-build-chain browser page
tests/                    unit, integration, security, golden and fault checks
```

## 3. Exact comparison contract

### Cohort key

A product identity is the normalized tuple:

```text
category | brand | model | ram_gb | storage_gb | network | pack_count | color
```

Normalization trims, collapses whitespace, and uses Unicode case folding. Requested fields must match exactly. A missing requested attribute is not inferred. `128GB` and `256GB`, `4G` and `5G`, and pack 1 and pack 2 are never one product identity. Condition is a separate comparison lane: `new`, `refurbished`, and `open_box` are never mixed for ranking, while the physical product identity remains canonical.

The system clusters first. It selects one corroborated cohort for the request and ranks only offers in that cohort. Different products are not mixed into H1 ranking. Product-level recommendation selection remains separate from merchant ranking; if the system cannot establish a single verified cohort, it returns no recommendation.

The committed fixture has 180 raw observations. Condition-aware `_deduplicate`
retains 178: `offer-0001`, `offer-0011`, and `offer-0012` share one duplicate
fingerprint, so `offer-0001` is retained and the other two are removed. The fixed
clock then produces 3 verified ranked offers and 175 excluded evaluations. These
are post-deduplication counts; no fixture records are removed to make the totals
look consistent.

### Identity tiers

1. Tier 1: validate GTIN length, digits, and checksum when a GTIN is supplied.
2. Tier 2: use normalized brand + MPN, with brand + model as the fixture fallback.
3. Tier 3: compare the requested and observed normalized attributes.
4. Tier 4: an advisor/AI hint can only create `NEEDS_REVIEW`; it cannot pass an identity gate.

Two offers from one source cannot corroborate each other. Retries, mirror URLs, and source-owned record IDs do not create independent evidence. Source identity is the registry `source_id`.

### Source adapter contract

`adapters/contracts.py` defines `SourceAdapter`: an adapter has a stable name and
returns a validated `LoadedFixture` containing normalized offers plus `SourceInfo`
metadata. The existing fixture adapter is the default and only configured source.
The orchestrator accepts an injected adapter for deterministic contract tests, but
does not make network requests or fall back from an injected source failure to
fixture prices. A failure follows the existing retry/quarantine allowlist and ends
in abstention. Future live sources require separate approval for endpoint, access,
permissions, retention, and merchant domains.

## 4. Gate policy

Gates are evaluated before trust scoring and before ranking. Each card always contains G1 through G7 with `PASS`, `FAIL`, or `ABSTAIN`, a stable reason code, and a human detail.

| Gate | Pass condition | Typical reject or abstain codes |
|---|---|---|
| G1 permissions and exact variant | Required fields are permitted, query fields and selected cohort match, and condition lane matches | `PERMISSION_DENIED`, `PERMISSION_REVOKED`, `VARIANT_MISMATCH`, `CONDITION_LANE_MISMATCH`, `DIFFERENT_COHORT` |
| G2 identity | GTIN checksum valid when present; fallback identity exists | `INVALID_GTIN` |
| G3 money | Decimal/minor-unit price, same currency, known total under budget | `CURRENCY_MISMATCH`, `OVER_BUDGET` |
| G4 freshness | `source_ts` is not future or stale; promo active; shipping and fees known | `STALE_SOURCE_TIMESTAMP`, `FUTURE_SOURCE_TIMESTAMP`, `PROMO_EXPIRED`, `PROMO_NOT_STARTED`, `SHIPPING_UNKNOWN`, `UNKNOWN_FEES` |
| G5 anomaly | effective cost belongs to median/MAD peer cluster | `ANOMALY_MODIFIED_Z`, `INSUFFICIENT_ANOMALY_SAMPLE` |
| G6 evidence | at least two distinct approved source IDs in the cohort | `INSUFFICIENT_INDEPENDENT_SOURCES` |
| G7 trust boundary | approved source, exact HTTPS hostname, country, stock, new condition, closed breaker | `UNAPPROVED_DOMAIN`, `INVALID_MERCHANT_URL`, `URL_DOMAIN_MISMATCH`, `REDIRECT_TRICK`, `COUNTRY_RESTRICTED`, `OUT_OF_STOCK`, `SOURCE_BREAKER_OPEN` |

A gate failure excludes the offer from the verified lane. An abstention is not silently turned into failure or success. Unknown shipping or fees has no effective cost, is labelled `UNKNOWN_FEES`, and cannot be budget-verified. A promotion where `promo_valid_until <= now` is expired; its promotional price is rejected. `price_minor` already includes discounts. `verified_discount_minor` is a separately proven fact and is never subtracted again.

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

- `source_snapshots`: permission-filtered snapshot projection, payload hash, capture time, registry snapshot, and retention-redaction marker;
- `runs`: run ID, query, policy/rules/review versions, injected clock, snapshot ID, registry snapshot, review decisions, state, result, result hash;
- `audit_events`: append-only hash chain with event type, actor, previous hash, and event hash;
- `source_health`: breaker state, dead-letter count, and update time.

Replay inputs are complete: immutable permission-filtered source snapshot, source-registry snapshot, query, policy version, rules hash, review version/decisions, and clock. Replay reads that saved snapshot directly and does not consult the current fixture path. A policy or review-version mismatch returns `MISMATCH` rather than silently changing history. The canonical replay bundle is hashed with RFC 8785 JCS, never a hand-written sorted JSON function. The two committed JCS vectors are tested, including Unicode and number normalization.

The run state machine is persisted after each idempotent step:

```text
SNAPSHOT → NORMALIZE → GATES → ANOMALY → RANK → EXPLAIN → AUDIT
```

The source registry also carries field-level permission names, explicit `revoked_fields`, and `retention_days`. A scheduled `purge` redacts expired raw payloads, retains provenance hashes and decisions, and reports the number of redactions. Restricted values are not written to snapshots, result cards, or audit payloads.

Audit verification checks both the local chain and the external checkpoint at `var/audit_checkpoint.json`, which anchors the expected count and terminal hash. This detects tail deletion and head mismatch. It is tamper-evident, not absolutely tamper-proof if an attacker can replace both the SQLite database and checkpoint.

## 9. Source health and recovery

The fixture connector has no network dependency. The Break-it UI control injects a timeout once or a persistent failure. Recovery can use only:

```text
retry, quarantine, expire, rollback, notify
```

A one-time timeout retries idempotently and produces the same verified result. A persistent failure is quarantined and results in `ABSTAIN`; it never becomes a guessed price. Breaker state and dead-letter count remain visible in the UI and API.

## 10. Golden dataset and quality gates

`tests/fixtures/golden_offers.json` contains 180 raw offers, 178 after
condition-aware `_deduplicate`, one duplicate fingerprint group, three verified
ranked peers, and 175 excluded evaluations. The test suite asserts:

- exact variant only in the ranked lane;
- near-miss 128/256, 5G/4G, and pack 1/2 rejection;
- invalid GTIN, stale, future, 100x, duplicate, unapproved, redirect, country, stock, promotion, shipping, and currency handling;
- permission denied/revoked, condition-lane separation, exact HTTPS URL validation, and non-clickable fixture links;
- raw/deduplicated fixture accounting and the source-adapter contract;
- no source may corroborate itself;
- Decimal money arithmetic and strict Pydantic;
- median/MAD including zero MAD;
- JCS golden hashes and hash-chain verification;
- audit changed/middle-deleted/reordered/tail-deleted detection and snapshot-only replay;
- fault retry, quarantine, and honest abstention;
- API health and static serving; and
- full cost breakdown including `UNKNOWN_FEES`; and
- an offline `make test` under the test budget.

## 11. Delivery and honest boundary

`Dockerfile`, `docker-compose.yml`, `.python-version`, `uv.lock`, `.env.example`, Makefile, README, and tests are committed. The runtime is pinned to Python 3.12.11. `make docker-test` is the clean-image check when Docker is available; Docker availability must be reported honestly. The image installs all runtime and test dependencies in the project image; no global dependency or credential is required.

The current adapter is intentionally fixture-only and not production-ready. It does not claim retailer coverage, currency conversion, missing-attribute inference, or advisor accuracy. A future connector must preserve the same schemas, permission checks, sanitized snapshots, provenance, gates, URL rules, and replay behavior before it can be used for customer decisions.
