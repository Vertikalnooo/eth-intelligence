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
Read-only Python API deployed on Render; `/health` and `/market/eth` are implemented. Market data availability and automatic deployment are under verification. No agents, database, or automated trading deployed.

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
