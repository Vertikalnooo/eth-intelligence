# ETH Intelligence — Stage 2 evidence-based acceptance report (2026-11-22)

**Decision: STAGE 2 OPEN — NOT ACCEPTED for completion or Stage 3.**
This is the research-side assessment, not an approval to deploy, merge,
subscribe to any exchange, enable recommendations or execute trades.

## 0. Change-control provenance

- Repository: https://github.com/Vertikalnooo/eth-intelligence
- Main snapshot: `b86fe378e7c0fbf19044a24a17c552bbe63b319a`.
- Research baseline: Draft PR #28 `fa4d282525d5f81680ece0fb54d0b5cb6b9bebe5`.
- PRs #20–#28: open Draft, unmerged at review time.
- This follow-up uses a separate research branch and Draft PR; no writes to
  earlier PRs, `main` or Render.
- CI basis: PR #28 run #289 (305 tests, success) and this follow-up's
  separately recorded final-head GitHub Actions result.

## 1. Recovered Stage 2 scope

The agreed Stage 2 is data quality and reliable read-only market ingestion:
Hyperliquid ETH/USDC perpetual `markPx`, REST `metaAndAssetCtxs`,
WebSocket investigation, validation, fail-closed stale/unsupported data,
cache and bounded 429 backoff/recovery, read-only API `/health`,
`/market/eth`, `/market/status`, GitHub CI and Render health.
No trade execution, no trading agents or uncontrolled self-modification.
Historical candles, volume analysis and agents belong to later stages.
The user must explicitly approve completion before any Stage 3 work.

## 2. Verified source and runtime evidence

| Requirement | Source / verification | Evidence / outcome | Status |
| --- | --- | --- | --- |
| Production source | `main/app.py` | `POST /info` `metaAndAssetCtxs`, map unique ETH universe index and `markPx` | CODE CHECKED |
| API routes | `main/app.py`, earlier Render request logs | `/health`, `/market/status`, `/market/eth`; logs show /health 200 and /market/eth 200 on 2026-11-20 | HISTORIC LIVE EVIDENCE |
| Production deny-all | `assess_market_recommendation` and its tests | `recommendation_eligible=False` even when caller claims authenticated fields | CODE + CI PASS |
| Rate limit / recovery | `cached_eth_market` and tests | Lock, monotonic cache, 429 bounded exponential cooldown and request interval; failure isolation | CODE + CI PASS |
| Render API deploy | `eth-intelligence-api` in user-confirmed My Workspace | Live deployment `b86fe378...` from 2026-11-20; branch main | RENDER METADATA PASS |
| Render API CI gating | Render service config | `autoDeployTrigger=commit`, not evidenced as `checks_pass`; no forced changes | BLOCKED / CONFIG RISK |
| Render WS test | `eth-intelligence-ws-test` | Historical two sessions, controlled reconnect, 2 messages each on allMids; MID, **not mark** | HISTORIC ONLY |
| WS mark investigation | Prior one-shot approved 30s ETH `activeAssetCtx` test | 30 ETH messages in earlier run; no authenticated update timestamp | HISTORIC ONLY — DO NOT REPEAT |
| Source mark-update timestamp | Official WS schema and official SDK REST model | `markPx` exists, but no independently authenticated timestamp uniquely bound to this update established | BLOCKED |
| Operational Freshness | PR #21+ offline diagnostics | Monotonic age, clock domains, REST/WS diagnostics, connected epoch | SYNTHETIC CI PASS |
| Exchange Freshness | PR #20+ research | No real trusted mark event evidence and no trusted clock provider | UNVERIFIED |
| Replay | PR #20–#28 | SQLite atomic local claims, duplicate/conflict; no multi-host or tamper guarantees | SYNTHETIC ONLY |
| Unified offline acceptance | PR #27–new branch | All components evaluated without authorizing acceptance or recommendations | SYNTHETIC CI |
| Live end-to-end mark ingestion into research modules | Not deployed | Existing offline modules are not wired into production API | BLOCKED |
| Render reliability under faults | Not independently measured end-to-end on deployed SHA | Historical partial logs, no bounded prolonged ETH mark uptime acceptance | BLOCKED |

### Upstream authority / timestamp semantics

Official Hyperliquid WS subscriptions:
https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions

Official Hyperliquid Python SDK REST context:
https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/api/info/assetctxs.yaml

REST receipt UTC, WS local monotonic receipt, book/trade/block timestamps,
opaque event IDs or positive REST/WS price agreement DO NOT authenticate
the exchange-side update time of `markPx`. If that evidence does not exist,
`exchange_mark_state=UNVERIFIED` must remain permanent; never invent a
`VERIFIED` branch.

## 3. Last audit P2 — no SQLite I/O under process-wide domain lock

Before remediation, PR #28 held a process-wide RLock while issuing an
optional SQLite replay claim. SQLite has an up-to-5000-ms busy timeout,
and this could unnecessarily stall revoke/rotate.

This branch removes **all** replay I/O and mutations from the unified
`inspect_stage2_offline` function. The process-local RLock now covers
only in-memory calculations and adapter/clock-domain state; no sqlite,
network, blocking disk access or remote calls are executed inside it.
Even `allow_synthetic_replay_claim=True` does not consume a key, for
compatibility and safety. A requested replay is returned as
`replay_result=UNAVAILABLE` and `synthetic_replay_disabled`.

The isolated `integration_harness.replay_diagnostic` remains available
only for EXPLICIT synthetic experiments with temporary local SQLite
storage; it never becomes admission proof or an authenticated binding.

The report exposes `binding_structurally_matches` for exact matching
of synthetic observation identity and price; this is NOT issuer proof.
All reports from this unified function: `accepted=False`,
`recommendation_eligible=False`, `stage2_complete=False`.

## 4. Test matrix and mandatory gates

**Offline acceptance suite:**
- Valid fresh and matching observations still deny recommendations.
- Wrong price, NaN, future monotonic sample, wrong domains, stale,
  degraded, disconnected, old WS epochs and forged event/update fields.
- Clock-domain fork/restart/revoke/rotate, process-local snapshot lock.
- Malformed claim metadata precedes stale/disconnect classification.
- Structurally mismatched binding never authenticates a price.
- SQLite claimed/duplicate/conflict/unavailable checked **separately**.
- Replay never touched by unified harness, including when SQLite is
  exclusively locked; revoked domain cannot be held up by SQLite in harness.
- Parallel standalone replay claims check one atomic claimed result.
- Explicit malicious admission return causes test failure, never allows
  an accepted report.
- Primary API remains read-only and recommendation-ineligible.

**Before declaring Stage 2 complete, all of these must be resolved:**
1. A documented explicit architectural decision: either acquire genuinely
   independent authenticated exchange mark update evidence, or approve a
   deliberate operational-only scope with verified exchange freshness always
   UNVERIFIED and recommendations blocked.
2. Check production CI-deploy policy. Current `autoDeployTrigger=commit`
   cannot be reported as `After CI Checks Pass` without corrective,
   separately approved config action and verification.
3. Independently verify the exact deployed SHA and endpoint outputs
   under controlled read-only conditions. Historical logs are not
   current end-to-end acceptance.
4. Decide lifecycle/replay operations: trusted UTC, health thresholds,
   multi-host/restart/replay persistence, data gaps and resilience.
5. Review any approved future production wiring as a separate change,
   with security tests, rollback plan and clear no-trading boundary.
6. Complete a final independent Stage 2 audit with exact SHA, CI logs,
   relevant Render evidence and zero unresolved high-severity blockers.
7. Obtain the user's **explicit Stage 2 completion approval**. Do not
   begin Stage 3 without that approval.

## 5. Bounded live test proposal (NOT executed)

All probe operations require a separately scoped go-ahead; prior one-shot
`activeAssetCtx` 30-second permission was single-use and MUST NOT be
reused. Re-evaluate whether another WS test is needed before any new one.
No scripts run or network requests to Hyperliquid were made in this PR.

A future narrowly approved verification should:
- Fix a specific SHA, target Render service and exact allowed paths.
- Specify max duration, hard timeout, max request count, exact ETH-only
  subscription and read-only methods, no credentials, no orders.
- Sample only local observation ages; do not forge exchange timestamps.
- Abort on 429, unexpected channels, invalid market, reconnect gaps,
  unknown freshness, service errors or approaching rate limits.
- Store just status codes, anonymized timing and reason codes; no secrets.
- Have a deterministic rollback/deactivation plan and independent review.
- Avoid changing the WS test service auto-deploy branch without approval.

## 6. Honest conclusion

**Offline research:** substantial evidence and passing CI, but not a trusted
real-time price-verification system. **Render:** exact deployed SHA and
historic request / WS mid-price tests verified from read-only metadata/logs,
not new current-traffic probes. **User approval gate:** OPEN.

Do not merge automatically, change production, claim exchange provenance,
enable recommendation eligibility, or start Stage 3. This report does not
close Stage 2.
