# Stage 2 unified offline audit remediation — PR #27 follow-up

Source is exact PR #27 HEAD `231deda1f7864afb695d1405ee1645a13dc30edf`.
Research only: no production, Render, exchanges, endpoint calls, trading, or
modification of source PR #20–#27 or main.

## P1 — Replay side-effect ordering

The unified `inspect_stage2_offline()` is **read-only by default**.
A replay binding claim is attempted only when all of the following hold:

- Caller explicitly passes `allow_synthetic_replay_claim=True` (actual bool).
- An offline SQLite store and binding are supplied.
- The issued clock domain is active for the entire local-process diagnostic,
  and the receipt-only adapter succeeds.
- REST transport is FRESH; REST/WS consistency is CONSISTENT, which requires
  both receipts FRESH, the same monotonic domain and valid connected WS epoch.
- Caller claim was structurally validated as UNVERIFIED (not INVALID).
- Binding matches exactly the current REST observation identity, venue,
  symbol, mark type and Decimal mark price, and uses allowed `mark_update`
  semantics and valid event key.
- Any explicitly asserted event ID or asserted UTC update time agrees with
  the binding. A caller's assertion does **not** authenticate the event.
- The resulting binding digest is canonically well formed.

If any precondition fails, the *unified* harness does not call replay and
cannot write a key. It returns `replay_result=UNAVAILABLE` plus
`synthetic_replay_not_authorized` (or `replay_not_requested`).
A successful synthetic CLAIMED status can still occur only with explicit
opt-in and does not imply authenticated exchange provenance.

The low-level `SQLiteReplayStore.claim_once` remains an internal
diagnostic primitive; this policy applies to the unified harness only.
**No trusted real admission path has been built.**

## P2 — Binding identity and synthetic evidence limits

`_binding_matches_rest()` checks that all structural binding fields belong
to this exact observation. A matching `VerifiedBinding` is still
caller-supplied and entirely untrusted. The local UTC receipt time is never
silently substituted for an exchange update timestamp. No replay claim
creates a real verified market event.

## P2 — Atomic snapshot vs clock revocation

`integration_harness.clock_domain_guard` holds the process-local RLock
around the complete unified report. Concurrent revoke/rotate waits until the
report completes; the result is internally consistent with domain state
during its evaluation. This is only **process-local**, not a distributed
transaction or trusted clock. SQLite operations can delay revoke up to the
storage timeout. Fork protection still uses `register_at_fork`.

## Regression and acceptance

New tests cover invalid, stale, disconnected, mismatch, wrong epoch, wrong
binding observation ID, wrong binding price, event/timestamp mismatch, bool
opt-in enforcement, replay store missing, repeated replay and concurrent
revoke. The old opt-in tests were updated to request synthetic replay
explicitly. Run full `python -m unittest discover -s tests -v` and
validate the exact PR HEAD in Actions logs.

A green CI still does not establish real Hyperliquid per-mark update
authentication, reliable WS/Render operation, a trusted clock, cross-host
replay, or production acceptance. `accepted=False`,
`recommendation_eligible=False`, `stage2_complete=False` at all times.
Stage 2 stays OPEN until independent audit and explicit user approval.
Stage 3 stays NOT STARTED.
