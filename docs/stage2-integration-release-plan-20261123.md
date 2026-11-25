# Stage 2 — integration readiness and rollback (2026-11-23)

> Historical snapshot. For the verified 2026-11-25 production baseline and
> remaining acceptance items, see [Stage 2 Final Hardening](stage2-final-hardening-20261125.md).
> Earlier SHA, Draft/merge and deployment statements below refer to their original review.

**PRE-MERGE REVIEW ONLY. No merge, deployment, new WebSocket test or Stage 3 approval.**

Project: Vertikalnooo/eth-intelligence. The project owner explicitly approved
an operational-only, read-only research architecture. Approval is NOT approval
to publish changes, activate trading recommendations, or close Stage 2.

## Baseline at preparation time

| Target | Verified configuration |
| --- | --- |
| GitHub main | SHA b86fe378e7c0fbf19044a24a17c552bbe63b319a |
| Draft PR #30 | Prior HEAD 72a9e96a8d0e04fac105840afb696dfbfc97d184 |
| Production Render | eth-intelligence-api, checksPass, /health, branch main |
| Production deployment | dep-dc1l9pec6qcc73b5qqeg, same main SHA, live |
| Isolated WS service | eth-intelligence-ws-test, suspended by user |
| WS evidence | ETH activeAssetCtx public read-only, 2 successful sessions, 1 reconnect, 4 frames, 2 mark receipts, 2912 ms |

These are snapshots, not guarantees of future state. Re-read GitHub HEAD,
Render settings, deployment and logs before executing any release.

## Contract and implementation boundary

- Operational Freshness measures LOCAL monotonic receipt age, REST request
  windows, WebSocket session/epoch and REST/WS diagnostic consistency.
- Verified Exchange Freshness remains UNVERIFIED: a local fresh receipt,
  matching prices and upstream markPx alone do not authenticate mark update
  time or the exchange clock.
- Unified research results remain accepted=False,
  recommendation_eligible=False, stage2_complete=False. Synthetic SQLite
  replay is not a trusted multi-host production admission or clock mechanism.
- Production recommendation gating remains fail-closed even if caller
  metadata claims verification. No orders, private channels, trade execution
  or automatic research admission may be added under this approval.
- PR #30 originally changed 43 files / 66 commits and did NOT change
  app.py or render.yaml; it adds research packages, tests, CI and documents.
  The current API does not import these research packages or run a continuous
  WS collector. Merging PR #30 alone would NOT activate operational
  freshness reporting in production.
- The bounded isolated WS probe is not a continuous source-health test.
  Its runner is unsafe to resume without hardening its ephemeral /tmp marker.
  KEEP eth-intelligence-ws-test SUSPENDED.

## Pre-merge checks — do not merge without separate authorization

1. Freeze exact PR HEAD, main SHA, full files/commit ancestry, and reviewed
   diff. Confirm no unexpected changes to app.py, production start command,
   Render config, credentials, public/private channels, or runnable scripts.
2. Require successful GitHub Actions on final HEAD: push AND PR merge-ref,
   full Python 3.12 unittest discovery, recorded Ran N tests / OK and
   confirmed Python socket fence. This fence is not a full OS sandbox;
   Render's build environment is not asserted to inherit it.
3. Confirm static/run-time release-boundary tests: only GET/HEAD handlers
   on current app, no production imports of research modules, exact-false
   exchange and recommendation flags, forged timestamps rejected.
   These tests are guardrails, not comprehensive security verification.
4. Record production service ID, deployed SHA/deploy ID, checksPass,
   /health path and pre-release fail-closed API behavior. Only bounded
   read-only GET /health and GET /market/status are in the standing
   preflight contract. Never call /market/eth or Hyperliquid without
   separately scoped permission.
5. Decide whether Stage 2 completion requires deployed live wiring of
   operational freshness; it does not exist in this PR. Such work must
   be separately designed, audited and approved before claiming it.
6. Ask the user for separate permission to merge AND for the resulting
   automatic Render production deploy. The architectural approval
   does not imply either permission.

## Future deployment, ONLY after explicit user approval

- Verify all gates, merge-ref and main at the expected SHAs.
- A merge to main is likely to trigger a Render build/deploy because
  autoDeployTrigger=checksPass; even research-only files can redeploy
  the entire service. Do NOT bypass CI on failure.
- Independently verify deployed commit, service health, GET /health
  and GET /market/status HTTP 200 and JSON, false exchange-freshness
  and false recommendation eligibility. Check Render deploy and logs.
- No unsupported claim of verified exchange mark timestamp, 24/7
  source reliability or integration into live API is permitted.
- Obtain a separate Stage 2 closure decision; Stage 3 is prohibited
  until the owner explicitly approves closing Stage 2.

## Contingency/rollback plan — not to be exercised now

1. If a future release causes 5xx, failed health checks, unexpected
   subscription, inconsistent API schema, or eligibility=True: declare
   FAIL; record exact SHA, deployment ID, timestamps and reason codes.
2. STOP release work and request separate authority for a production
   recovery operation. Do not restart the suspended WS service.
3. Determine whether Render permits restoring the prior build for
   main SHA b86fe378e7c0fbf19044a24a17c552bbe63b319a; do not
   assume rollback is one-click or available. If approved, restore
   and verify the actual deployed SHA, health, and fail-closed status.
4. Independently request approval for a GitHub revert PR if needed:
   rolling back Render does not reset main; reverting main can itself
   initiate a separate CI-gated deployment. No force-push or silent
   changes to production are allowed.
5. Document incident and re-run full pre-release safety checks before
   asking for another deployment decision.

## Status

Architecture: APPROVED (operational-only, read-only).
Release and merging PR #30: NOT APPROVED.
Research CI from earlier HEAD: 352/352 PASS; require new exact HEAD CI.
WS test service: SUSPENDED; no resume authorized.
Stage 2: OPEN. Stage 3: NOT STARTED.
