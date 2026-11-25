# Stage 2 REST Operational Freshness (2026-11-24)

> Historical snapshot. For the verified 2026-11-25 production baseline and
> remaining acceptance items, see [Stage 2 Final Hardening](stage2-final-hardening-20261125.md).
> Earlier SHA, Draft/merge and deployment statements below refer to their original review.

DRAFT ONLY: No merge, Render deploy, exchange request, WS subscription,
orders or Stage 2 completion is authorized by this research candidate.

## Baseline and scope

- Exact main at preparation: f66425d216c5a3005dada3f25ecdb595d324f676.
- Current Render production has a checksPass-gated deployment from main.
  Merging the Draft PR would trigger production build/deploy and therefore
  requires a separate, explicit authorization.
- Existing GET /health, GET /market/status, and GET /market/eth and its
  upstream REST/cache/429 behavior are retained. No new market polling.
- Only GET /market/status is extended with process-local REST receipt-age
  diagnostics. That route makes NO network request and does not modify state.
- Only freshness.assess_transport and its validated data model are reused
  from research, via the minimal rest_operational adapter. Research admission,
  replay, provenance, trust modules and SQLite remain disconnected.

## API additive diagnostic contract

The original /market/status JSON keys are preserved. The additions are:
- stage2_complete: false
- rest_operational_freshness: an object with fields state, age_ms,
  clock_basis, source_channel, reason_codes,
  exchange_freshness_verified=false,
  recommendation_eligible=false, stage2_complete=false.

State semantics:
- NO_DATA: no successful observed REST receipt in this process, or a
  429 has invalidated both cache and receipt.
- INVALID: invalid metadata, backward/local clock anomaly, process domain
  change, or unavailable cache while age would otherwise appear fresh.
- FRESH: local monotonic receipt age <= 2000 ms and valid cache.
- DEGRADED: local receipt age > 2000 ms through 5000 ms and valid cache.
- STALE: local receipt age > 5000 ms; remains historical diagnostic even
  if cache has expired. Cache availability is always separately reported.
- age_ms is floor(elapsed monotonic ns / 1e6); null when no valid age.
- reason_codes include missing_exchange_provenance for every state.

A cache hit never renews the receipt timestamp. The source's own UTC
observed_at is not a trusted clock or exchange mark update time.
Local receipt FRESH is only an operational observation; NEVER proof that
markPx was newly updated at Hyperliquid.

## Fail-closed boundaries

- No network, background job, WS, new endpoint, private channel or order.
- The old production boundary test is strengthened rather than removed:
  the only approved new app import is rest_operational, which itself uses
  only the freshness research data model and pure transport evaluator.
  There is no production path into provenance, replay, trust_research,
  integration_harness or stage2_acceptance.
- Status and the nested diagnostic always expose
  exchange_freshness_verified=false,
  recommendation_eligible=false,
  stage2_complete=false.
- For malformed/forged source flags, snapshot is INVALID; no admission.
- On 429 the market cache and local receipt are invalidated together.
  Transient errors retain the last receipt but cannot produce fresh status
  after cache invalidation.
- Process-local monotonic clock identity does not establish distributed
  trustworthy time. Fork/restart state is not trusted. No 24/7 source
  uptime or independently authenticated exchange mark timestamp is shown.

## Tests and release gate

1. Offline Python 3.12 unittest discovery and Python socket guard on
   exact final HEAD; both push and PR merge-ref results must PASS.
2. New regression tests exercise age limits, stale/expired/lost cache,
   cooldown, forged metadata, monotonic reversal, process change, HTTP
   contract, concurrency and zero extra upstream fetches.
3. Independent read-only audit of exact diff, CI logs and safety.
4. Stop for a separate user decision before merging to main. Never
   deploy to Render, call /market/eth, resume the WS service, or contact
   Hyperliquid in the scope of this candidate.
5. If a future merge is explicitly approved, first re-check SHA/CI/Render
   gates and then perform read-only /health and /market/status smoke.
   Stage 2 remains OPEN; Stage 3 has not begun.
