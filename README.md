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

## Verified baseline — 2026-11-25

| Component | Last verified state |
| --- | --- |
| Production main | 038c7b47f07321067911bc1cf6abc50ab162b2e0 (PR #32) |
| Render API | eth-intelligence-api, live deployment dep-dc3ctk68bjmc73bnf9ag |
| Render deployment policy | checksPass; /health |
| Exact-main CI and Render build | 369/369 tests passed |
| Production REST operational diagnostics | GET /market/status, HTTP 200 and NO_DATA contract observed |
| Research integration | PR #30 merged; provenance/replay/unified evaluator remain offline-only |
| WS test branch | PR #31 merged, 72df9867ea436c15204630a43ad033dee892859b |
| WS test Render service | USER-SUSPENDED; last deployment b24d11a344c89ec0acfca8da9234c150338a91d4 still contains the old auto-probe runner |
| Verified exchange mark-update freshness | UNVERIFIED |

These are dated observations, not automatic evidence about any subsequent
deployment. Do not resume the old WS service. The corrected runner is in
the test branch, not deployed to that service.

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

See [current acceptance baseline and open items](docs/stage2-final-hardening-20261125.md)
and [REST operational contract](docs/stage2-rest-operational-freshness-20261124.md).
The final-hardening proposal is not authorization to merge, deploy, retire or
resume a service, perform live exchange tests or close Stage 2.

Historical research and rollout decisions:
[release candidate](docs/stage2-release-candidate.md),
[integration/rollback plan](docs/stage2-integration-release-plan-20261123.md).
Their historical snapshots are superseded by the dated baseline above.
