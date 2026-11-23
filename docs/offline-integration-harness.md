# Offline integration harness — PR #22

Pinned source snapshots:
- PR #20: 41f6a1bd333b8cd1975397c0670c5c71010d2f8d
- PR #21: 0dbbf93552e58acd927de096ccc8e4aed776808f

This independent Draft PR copies the research files from both branches and tests them together. It does not merge either source PR or modify main/production. No exchange connection, market subscriptions, order placement, or endpoint modifications.

## Test execution
The repository's standard unittest discovery executes both original suites and tests/test_offline_integration_harness.py. CI is the authoritative record for the combined test count.

## Boundaries
- to_market_observation returns an actual PR20 MarketObservation, not a lookalike dataclass. It maps receipt fields only and never asserts exchange provenance.
- new_clock_domain registers a random process-local token and PID. revoke_clock_domain and rotate_clock_domain explicitly invalidate issued tokens. This is a diagnostic guard, NOT authenticated monotonic clock provenance; callers must invoke revocation when sessions end.
- diagnostic_consistency requires both observations to be locally FRESH and WS session valid before comparing; invalid, disconnected, and stale cases return distinct diagnostic codes. Agreement is not exchange mark freshness.
- replay_diagnostic calls PR20's SQLite claim_once atomically and returns only a diagnostic ClaimResult. It is NOT a trusted replay-to-admission interface. The caller can still supply arbitrary values to standalone PR21 evaluate(), which permanently denies eligibility.
- combined_deny calls the actual PR20 acceptance gate and PR21 evaluate(), and never permits eligibility.
- PR20 inspect_offline_pipeline and legacy validate_provenance remain research-only and can return successful synthetic diagnostic results. No synthetic result may be accepted by a production gate.
- Exchange mark timestamp proof, trusted evidence provider, trusted UTC clock, durable deployment storage and a real admission integration remain unresolved blockers.

## Offline audit remediation (research only)

- Clock domains now have a process-local issued-token registry. `revoke_clock_domain` and `rotate_clock_domain` explicitly invalidate previous tokens; a forked process clears inherited registration. This is not an authenticated clock provider or a persistent process-wide lifecycle service.
- The typed adapter reuses strict `freshness._valid_observation` checks, including forbidden REST timing fields on WS records. Exchange timestamp claims remain untrusted and are never mapped as provenance.
- Diagnostic consistency distinguishes malformed inputs (`INVALID_INPUT`), disconnected WS (`DISCONNECTED`), stale transport (`STALE`), and valid degraded/out-of-window comparisons (`NOT_COMPARABLE`). No consistency result authorizes exchange freshness.
- Replay diagnostic accepts only the exact `SQLiteReplayStore` and structured `ReplayKey` with a 32-byte digest. Its return is still diagnostic-only; a caller-chosen digest does not prove exchange authenticity.
- Integration tests include revoked/rotated/forged domains, mixed-channel fields, invalid metadata, diagnostic reason specificity, forged replay stores, unavailable storage and replay conflicts. No changes to production endpoints or acceptance policy.

## Acceptance
Combined CI success proves the pinned research code and harness tests execute together. It does not prove live-source integrity, production readiness, or a completed Stage 2. Require independent security review and user approval before any further changes.

## PR #23 follow-up hardening (independent research branch)

Base: PR #23 HEAD `62c5e60b8d3d212d26d4fad8997a2fbc5ff51205`; source PRs and production are not modified.

- Clock-domain mapping, diagnostic comparison, revoke, and rotate serialize against the same RLock. Work that completed before revoke may exist as an untrusted diagnostic snapshot; after revoke returns no *new* successful domain-scoped mapping is permitted. A POSIX after-fork hook replaces both the lock and issued-token registry in the child. A newly started interpreter has no old registrations.
- `freshness.valid_observation` is the common validation contract for `freshness.compatibility.to_provenance_fields` and the typed `integration_harness.to_market_observation`; the typed adapter adds the local issued-domain check and never copies exchange timing as provenance. A raw `MarketObservation` remains receipt-only.
- `freshness.evaluate_rest_ws` checks both transport ages against the *same supplied monotonic now* and one explicit session; it distinguishes INVALID_INPUT, DISCONNECTED, STALE, NOT_COMPARABLE, CONSISTENT and MISMATCH, and emits side-specific reason codes. Bare `compare_rest_ws` remains an age-unaware primitive for legacy offline contract tests; never treat it as a freshness gate.
- `replay_diagnostic` no longer accepts a caller's bare ReplayKey/digest pair; it requires a structurally valid `VerifiedBinding`, derives the key and canonical SHA-256 digest in one place, and calls a concrete immutable-path SQLite store. This still does **not authenticate** the binding or exchange timestamp. Direct use of `SQLiteReplayStore.claim_once` with caller-supplied digests remains a legacy low-level diagnostic primitive, not an admission boundary.
- Store path is immutable at ordinary API level, resolved to an absolute path at creation; a claim uses SQLite `mode=rw` so an unexpectedly removed database cannot silently recreate as an empty replay store. SQLite BEGIN IMMEDIATE and duplicate/conflict semantics remain unchanged. This does not protect against external filesystem replacement, tampering, privileged Python reflection, or multi-host split-brain.
- `combined_deny` is deliberately an unconditional False, including when replay diagnostic returns CLAIMED. `accept_market_data` remains unconditional denial and `recommendation_eligible` remains False.
- Added offline negative tests for concurrent revoke/rotation, POSIX fork while a lock is held, fresh interpreter restart, malformed adapters, stale/disconnected/invalid-epoch pair diagnostics, replay conflicts and missing databases. Legacy tests using direct store-path mutation were adapted to immutable configuration.

Remaining trust blockers: real authenticated Hyperliquid ETH mark-update event/timestamp, independently trusted clock and verifier, trusted source health, durable cross-host replay policy, operational WS session lifecycle and a separately audited admission gate. Test coverage and green CI do NOT close Stage 2 or authorize trading.
