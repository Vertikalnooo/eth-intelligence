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
