# ETH mark provenance — offline research hardening (PR #20)

## Safety boundary
Pure Python research only. No live exchange, REST, WebSocket, orders, production
integration, or modifications to existing endpoints. This is NOT a verified
Hyperliquid mark timestamp implementation.

## C1: trust
There is **no audited production verifier**. Registry callbacks are accepted
only when `allow_synthetic_test_verifier=True` AND the method is
`synthetic_only`. Default policy denies every verifier, including injected
ones. Synthetic mode is exclusively for offline tests. This is not a
cryptographic trust anchor: callers controlling the policy can enable the
test mode. Production must never supply such a policy; a future separately
approved, non-injectable trust anchor is required.

## C2: replay
A verifier must return a `VerifiedBinding` with exact venue, symbol, price,
event ID, observation ID, timestamp and `mark_update` semantics.
The validator rejects a replay key already in `consumed_replay_keys` and
returns a candidate replay key only after successful validation.
**Important**: the validator is pure and does NOT atomically consume a key.
Concurrent callers can race. A future stateful gate must atomically reserve
and consume event keys and persist them across restarts. Until that exists,
the validator is NOT replay-safe for live use. An unchanged mark price may
legitimately recur; uniqueness is tied to verified event identity, not price.

## C3: timestamp
Only `timestamp_semantics='mark_update'` is accepted; block, snapshot,
trade, BBO, WS receipt or unrelated timestamps cannot be substituted.
An untrusted caller can still label a timestamp `mark_update`; a future
audited verifier must independently establish this semantic link.

## M1: clock
The caller supplies UTC clock time, uncertainty bound, synchronization source
and monotonic measurement marker. Missing or invalid metadata fails closed.
These fields are *claims*, not independent proof of clock synchronization.
A future trusted clock provider must authenticate them.

## M2: binding
A `VerifiedBinding` must match the observation and evidence exactly.
The test-only callback can forge a binding, so successful synthetic tests do
not establish authenticity.

## M3: recommendation gate
`recommendation_eligible` is **always false** in this module, including
successful synthetic checks. A future separate, approved gate must combine
provenance, atomic replay protection, transport health and other independent
risk controls. No automatic integration is permitted.

## Offline verification
Run `python -m unittest discover -s tests -p 'test_*.py' -v`.
32 original offline cases plus 20 adversarial security cases.
Do not merge until separately reviewed and approved.


## v1.1 offline hardening — isolated research, NOT production-ready

- `trust.py`: `NoTrustedEvidenceProvider` is the only accepted gate provider. No trusted Hyperliquid evidence provider exists. Legacy `EvidenceVerifierRegistry` and `allow_synthetic_test_verifier` remain for the original 52 compatibility tests; **they are not a production boundary**. The v1.1 acceptance gate refuses injected verifier objects, even if they return a forged binding. A future audited implementation must replace this sealed deny-all boundary via separate approval, never via caller flags.
- `replay.py`: immutable structured `ReplayKey` and persistent SQLite `BEGIN IMMEDIATE` claim with a unique primary key. Claim is durable within SQLite filesystem guarantees; no network or process-global state. Duplicate, conflicting digest, and unavailable store fail closed. For full production readiness: explicitly define event ID namespace and lifetime, multi-host storage, crash durability and state migration.
- `clock.py`: provider protocol and checked samples with UTC, approved source ID, monotonic validity, uncertainty and conservative drift allowance. A sample's claimed source is not authenticated by a cryptographic mechanism; no trusted real provider is shipped.
- `decision_gate.py`: separate `MarketDataAcceptanceResult` always denies while no real trust anchor exists. It never permits recommendations.
- `tests/test_provenance_hardening_v11.py`: 30 additional offline tests, including 8 concurrent claims and reopening the database. Tests are synthetic and cannot prove exchange provenance.

No live connections, orders, endpoint integration, or production changes. v1.1 is a safe design/test scaffold, not an operational market-data acceptance engine.

## v1.2 focused offline remediation

- The real `accept_market_data` path remains permanently fail-closed: no trusted exchange evidence or authenticated clock provider exists. Neither user-injected verifier nor synthetic clock may enable it. Legacy synthetic validator remains exclusively a research compatibility API and must not be imported by a production application.
- `inspect_offline_pipeline` is an isolated research diagnostic with **no** acceptance or recommendation fields. It checks binding equality, clock sample, freshness and atomically claims a replay key, but a synthetic binding does **not** establish authenticity. Do not use its `checks_passed` result to authorize market data.
- Replay namespace is fixed to `mark-update`; canonical SHA-256 digest includes version, venue, ETH perpetual market, namespace, event ID, observation ID, price, UTC timestamp and semantics. A digest proves consistency, not the source of the evidence.
- SQLite connections close explicitly; `BEGIN IMMEDIATE`, `COMMIT` and rollback handle transaction failure. Local crash-before-commit, crash-after-commit and multiprocess tests are limited to the test environment and cannot prove physical power-loss durability or cross-host guarantees. Persistent storage and filesystem semantics require independent review.
- Clock sample validation rejects malformed and stale inputs. `source_id` is not authentication; the real clock trust function always denies.
- 16 additional offline regression tests in `tests/test_provenance_hardening_v12.py`. No exchange, REST, WebSocket, orders or production endpoints are used.

Remaining blockers: authenticated mark-update timestamp for a specific Hyperliquid ETH mark, independently trusted clock, audited real verifier, and a separately approved production gate. No merge or Stage 2 completion is implied.
