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
