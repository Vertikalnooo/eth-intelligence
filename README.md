# ETH Intelligence

Personal ETH/USDC intraday analytical platform for Hyperliquid.

## Principles
- Analysis and recommendations only; never place orders automatically.
- Selective signals with explicit risk assessment.
- Future independent agents: market, macro/news, risk and review.
- Future forecast logging with timestamps, evidence, model versions and outcomes.
- Planned sources include Hyperliquid and Forex Factory, subject to access and terms.
- No uncontrolled self-modification.

## Development

Five gated stages. Test each stage and obtain explicit user approval before
proceeding. **Stage 2 OPEN; Stage 3 NOT STARTED.** Recommendations remain
disabled. The approved Stage 2 architecture is operational-only and read-only.

## Verified baseline — 2026-11-26

| Component | Last verified state |
| --- | --- |
| Production main | 5e1d27bf032ebd05c4d9ff2580f890f9e5cbdd24 (PR #33) |
| Render API | eth-intelligence-api, live deployment dep-dc441omc6qcc73cekg7g (manual recovery) |
| Render deployment policy | checksPass; /health; automatic trigger recovery not yet demonstrated |
| Exact-main CI and Render build | 383/383 tests passed |
| Production REST operational diagnostics | HTTP 200; NO_DATA and STALE observed live, cache expiry confirmed |
| Bounded live REST | One approved /market/eth invocation; ETH/USDC perpetual mark received |
| Research integration | PR #30 merged; provenance/replay/unified evaluator remain offline-only |
| WS test branch | PR #31 merged, 72df9867ea436c15204630a43ad033dee892859b; 47/47 offline CI |
| WS test Render service | RETIRED; user deleted; service lookup 404 and absent from workspace list |
| Verified exchange mark-update freshness | UNVERIFIED |

These are dated observations, not automatic evidence about any subsequent
deployment. The retired WS service must not be recreated or restarted as part
of the current scope. A future WS service needs a separately approved design.
The one-shot live REST authorization has been consumed.

## Production API

- GET /health: process liveness, not exchange health.
- GET /market/status: local cache, throttling and REST receipt-age diagnostics;
  does not fetch the exchange.
- GET /market/eth: existing read-only market fetch; may contact Hyperliquid.
  Do not use it for smoke tests without separate explicit exchange permission.

Local FRESH describes monotonic receipt age, not exchange mark update time.
The cache TTL is independent of the 2s/5s diagnostic thresholds.
The recommendation gate is deny-only.

## Deployment verification

Before any merge to main, obtain explicit approval of the merge and resulting
CI-gated Render deploy. Verify exact HEAD and base, full push and PR merge-ref
CI, deployed SHA and Render status. A documentation-only change may still
redeploy production. Do not bypass CI or perform an automatic rollback.

The bounded read-only smoke script defaults to plan-only. Actual HTTP execution
requires separate authorization; only /health and /market/status are allowed.
No /market/eth, exchange subscriptions or trading in that smoke scope.

## Stage 2 final hardening

See [current evidence and Auto-Deploy recovery plan](docs/stage2-auto-deploy-recovery-20261126.md),
[historical final-hardening review](docs/stage2-final-hardening-20261125.md)
and [REST operational contract](docs/stage2-rest-operational-freshness-20261124.md).
The documentation-only recovery candidate is not authorization to merge,
deploy, create services, perform live exchange tests or close Stage 2.

Historical research and rollout decisions:
[release candidate](docs/stage2-release-candidate.md),
[integration/rollback plan](docs/stage2-integration-release-plan-20261123.md).
Their historical snapshots are superseded by the dated baseline above.
