# Freshness v1.0 — offline research only

This isolated module is based on `main` and does not import PR #19 or #20. No REST/WS calls, market subscriptions, orders, live prices, endpoint modifications, or production integrations.

## Definitions

- **Transport freshness:** local monotonic receipt age in the same clock domain. `FRESH` <= 2000 ms, `DEGRADED` > 2000 and <= 5000 ms, `STALE` > 5000 ms. Thresholds are provisional research policy, not measured Hyperliquid guarantees.
- **Exchange mark freshness:** always `UNVERIFIED` without a claim; `INVALID_EVIDENCE` for injected untrusted claims. The documented ETH `markPx` feeds do not provide an authenticated timestamp tied to that exact update. No implementation of `VERIFIED_FRESH` exists.
- **WebSocket lifecycle:** each observation belongs to an explicit session epoch; disconnected sessions reject, and a reconnect does not rehabilitate previous messages. The module does not manage network sessions.
- **REST timing:** request start/end monotonic times must be ordered and precede receipt; they never become an exchange timestamp.
- **REST/WS consistency:** diagnostic-only comparison of observations within 2000 ms and the same clock domain. Relative price difference is in basis points using Decimal; threshold 5 bps. Agreement is NOT proof of authenticity or freshness.
- **Replay:** reported as a diagnostic input only. No claim is made that replay is atomically consumed here; no integration with the PR #20 SQLite replay implementation. `recommendation_eligible` is permanently false.
- **Clock:** local monotonic timestamps are meaningful only within one clock domain; `clock_domain_id` is a caller-supplied label and not authenticated. Restart/reconnect must invalidate previous domain-specific state.
- **Safety:** `evaluate` never returns a trusted exchange timestamp, verified provenance or eligibility. Reason codes are machine-readable. This is not a production admission gate.

## Tests and acceptance

`tests/test_freshness_offline_v1.py` contains 24 negative/boundary tests covering invalid values, boundaries, clock domains, WS disconnect and epoch, REST/WS mismatch and noncomparability, missing/forged exchange proof, and fail-closed admission. Full repository CI must pass. Future integration needs separate approval, real trust anchors, authenticated exchange update timestamps, replay guarantees and independent audit.

Limitations: no automatic source health check, cryptographic provenance, heartbeat, active WS network lifecycle, trusted clock synchronization, or exchange price freshness proof. These are deliberately out of scope. No user-facing production API changes.
