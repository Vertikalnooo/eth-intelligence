# ETH Intelligence

Personal ETH/USDC intraday analytical platform for Hyperliquid.

## Principles
- Analysis and recommendations only; never place orders automatically.
- Selective signals with explicit risk assessment.
- Multiple independent agents: market, macro/news, risk, and review.
- Log forecasts with timestamps, evidence, model versions and subsequent outcomes for evaluation.
- Sources include Hyperliquid and Forex Factory, subject to available access and terms.
- No uncontrolled self-modification.

## Development
Five gated stages. Test each stage and obtain explicit user approval before proceeding.

## Current status
Read-only Python API deployed on Render on main SHA b86fe378e7c0fbf19044a24a17c552bbe63b319a. Render currently has After CI Checks Pass and Health Check Path /health configured. The isolated eth-intelligence-ws-test service is suspended after one bounded ETH activeAssetCtx proof; never resume without separate approval. Research diagnostics in Draft PR #30 are not wired into the deployed app.py. There is no verified exchange mark-update timestamp or active trading/recommendation engine.

## Deployment verification
A documentation-only commit is used to verify that GitHub Actions passes and Render's `After CI Checks Pass` setting deploys the same commit automatically. Confirm the deployed commit SHA and check `/health` and `/market/eth` before declaring success.

<!-- Render auto-deploy diagnostic: On Commit trigger verification, 2026-11-20. No application code changes. -->
<!-- Render GitHub App installation retest: 2026-11-20. Documentation-only webhook trigger. -->


## Stage 2 offline release candidate (research only)

The cumulative, unmerged Stage 2 candidate and its review gates are recorded
in [docs/stage2-release-candidate.md](docs/stage2-release-candidate.md).
Operational Freshness is strictly local receipt diagnostics. Verified Exchange
Freshness remains UNVERIFIED; the real recommendation gate remains deny-only.
The Stage 2 offline tests use a CI socket fence with loopback-only fixtures.
No release, deployment, live exchange access or Stage 3 authorization is implied.

## Stage 2 integration / rollback decision

The user approved operational-only read-only research architecture on
2026-11-23, NOT merge, production deployment, closure of Stage 2 or Stage 3.
The current PR #30 candidate requires exact final SHA CI, diff and release
review. See [Stage 2 release plan](docs/stage2-integration-release-plan-20261123.md).
