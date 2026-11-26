# Stage 2 — REST One-Shot Safety (research candidate, 2026-11-26)

Stage 2 OPEN; Stage 3 NOT STARTED. No live grants, requests, deployment or merge authorized by this candidate.

## Scope and design

This change adds an isolated research module and offline tests. No changes to app.py, production routes, workflows, dependencies, Render or the retired WS service. There is no CLI, startup hook, background polling or automatic grant creation. The current public /market/eth route is unchanged and is NOT protected by this candidate. Do not use it to claim one-shot safety.

- Fixed direct HTTPS POST api.hyperliquid.xyz:443/info with only metaAndAssetCtxs; TLS verification; no environment proxy, redirect following, retry loop, alternate URL or trading operation. Non-200 is terminal even on 429/503. Only public market data; no credentials.
- Bound the raw body to 2 MiB, reject ambiguous length/transfer framing, wrong media type or encoding, truncated declared body, malformed UTF-8/JSON, duplicate keys, nonfinite constants, invalid universe or nonunique ETH, invalid bounded Decimal mark.
- A pre-existing trusted SQLite ledger binds a unique opaque ID to the exact operation and an expiration. BEGIN IMMEDIATE + synchronous FULL; commit the one-attempt reservation before constructing the transport. No ledger/grant creation by executor. Missing, expired, consumed, corrupt, locked or uncertain ledger: deny. Errors do not restore the grant. The reservation is not proof that bytes reached the exchange.
- One immutable local receipt is captured with the existing rest_operational adapter. A research-only local cache-window model uses 30s from receipt; not the production cache. Collect four local samples immediately, at target receipt age 3s, 6s and 31s; require actual FRESH, DEGRADED, STALE with cache valid, STALE with cache expired. Missing any window fails; never refetch. All exchange freshness and recommendation flags remain false. No synthetic timing may be presented as live evidence.

## Explicit limitations / acceptance blockers

1. This is a research candidate, not production integration. Existing app route remains outside this guard; do not conduct live tests until the isolation/entrypoint and operator-controlled grant provisioning are separately reviewed and approved.
2. The trusted ledger must be a single canonical durable file, protected against replacement, copying, restore/rollback and unauthorized writes, shared by all relevant callers. A fresh object/process with the same file cannot reuse a consumed grant. This is not distributed or tamper-resistant authorization. A filesystem/DB administrator can bypass it. No durable Render storage or live provisioner has been approved or configured.
3. Socket timeout (8s) plus monotonic elapsed checks are not a hard total wall-clock deadline: DNS, TLS, headers or an individual blocking read may overrun; a separately supervised killable worker and a bounded end-to-end verifier must be reviewed before live execution. No guarantee of exactly one received request or upstream/intermediary traffic accounting. The guarantee is at most one direct client POST attempt per grant in the trusted local process/store boundary, tested on loopback, not exchange telemetry.
4. Lifecycle collector verifies only process-local timing and a modeled cache window, not exchange mark timestamp or actual deployed cache. No live FRESH/DEGRADED evidence or verified exchange provenance. Its inputs are trusted local observations, not signed receipts or remote attestation.
5. 2 MiB body cap and strict framing are conservative unvalidated live compatibility decisions. Oversize/unsupported response must fail closed; do not loosen limits or repeat a live call without approval.
6. No Stage 2 closure, no Stage 3 work and no recommendation eligibility. Full exact-HEAD push and PR merge-ref offline CI and independent review required before considering next steps.

## Verification

Offline tests use synthetic SQLite ledgers in temporary directories and counted HTTP requests to 127.0.0.1. The HTTPS constructor is substituted with loopback transport; no TLS/network claim about Hyperliquid. No real grant is created, no external exchange request, no WS. Existing tests and Python network fence remain unchanged. This is not an OS-wide sandbox.

Before any future live one-shot: separate design/implementation review for the hard deadline and trusted ledger provisioning; explicit approval of integration, merge and any CI-gated deploy if applicable; exact SHA checks; separate one-shot authorization; default deny; verify one durable reservation, bounded local observations and all denial flags. Do not silently replace a lost grant or retry a failure.
