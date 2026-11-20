# Verification baseline — 2026-11-20

Scope: current gated stage only. No approval to proceed to the next stage is implied.

## Confirmed
- GitHub Actions checks passed on the error-hardening PR (user-provided screenshot).
- Render service `eth-intelligence-api` deployed commit `fedf138fe22efbe421d107ce43e85384c84ae062` automatically (`trigger: new_commit`, `status: live`).
- User-provided browser screenshots: `/health` returned `status: ok`, service `eth-intelligence`, version `0.2.1`; `/market/eth` returned a Hyperliquid ETH mark-price response.
- Current code returns `exchange_timestamp: null` and `exchange_freshness_verified: false`. The `observed_at` field is local fetch time, NOT exchange tick time.
- Current tests simulate HTTP errors, network outages, timeouts, malformed payloads, invalid prices, duplicate ETH entries, and health independence from market upstream.

## Not yet verified
- Exchange-origin timestamp/freshness for the mark price.
- Actual Hyperliquid outage behavior in production (mocked failure tests do not establish this).
- Sustained error-rate behavior, recovery, alerting, and real-world request latency.
- Render free instance can sleep when idle; a slow cold start does not imply an upstream outage.

## Next verification gates (do not deploy unsafe fault injection)
1. Inspect Hyperliquid official API schema and determine whether `metaAndAssetCtxs` provides an exchange-origin timestamp for the selected mark price. If not, explicitly retain `exchange_freshness_verified: false` and investigate a timestamped feed or cross-check, without inventing freshness.
2. Add reproducible integration probes that distinguish application health from upstream availability, with bounded timeouts, no cache, and sanitized error bodies.
3. Exercise controlled failures using a local fake upstream or isolated staging environment: DNS/network failure, HTTP 429/500/503, timeout, invalid JSON, malformed payload, recovery.
4. Confirm `/market/eth` returns HTTP 503 and `market_data_unavailable` without stale price, while `/health` remains independent. Verify recovery after upstream is restored.
5. Run CI, review changes, and verify automatic Render deployment only after passing checks. Request explicit user approval before moving to the next development stage.

## Safety
- Read-only analysis only. No trading, order placement, or production outage injection.
- Do not treat local fetch time as exchange tick time.
