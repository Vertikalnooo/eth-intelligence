# Stage 2 unified offline acceptance harness — research only

**Base snapshot:** PR #26 HEAD `6145b571e16074dd4f3eea71e51223bbd82a33d0`.
**Scope:** no live exchange access, no market subscriptions, no orders,
no Render, no production API changes, no edits to PR #20–#26 or `main`.
All tests use synthetic fixtures, local SQLite temporary files, and the
existing self-contained offline modules.

## What runs together

`stage2_acceptance.inspect_stage2_offline` composes the following actual
research components, rather than test-only lookalike implementations:

1. `integration_harness.valid_domain` and
   `to_market_observation`: issued process-local domain and receipt-only
   mapping; no exchange update timestamp is minted.
2. `freshness.evaluate` + `evaluate_rest_ws`: age of both REST/WS
   observations, single caller-monotonic clock, session epoch, connection,
   consistency and machine-readable reason codes.
3. `trust_research.inspect_mark_update_claim`: first validates all caller
   metadata and classifies claims as INVALID/UNVERIFIED; forged event IDs,
   opaque evidence or claimed timestamps cannot be independently verified.
4. `integration_harness.replay_diagnostic`: optional SQLite synthetic
   binding claim, including duplicate/conflict/unavailable checks. A
   CLAIMED result does **not** authenticate exchange events.
5. `provenance.decision_gate.accept_market_data`: permanent deny-all,
   irrespective of synthetic results.

The resulting frozen `Stage2OfflineReport` always has
`accepted=False`, `recommendation_eligible=False` and
`stage2_complete=False`. Verified exchange provenance never arises
from local freshness, agreement, replay or a synthetic verifier.
The optional replay input can consume a key in a **local test database**;
use a fresh temporary database for an independent test run.

**Non-atomic diagnostic snapshots:** observations, clock-domain checks and
replay claims are not an atomic operation. Concurrency protections in the
clock adapter do not turn this report into a trusted admission boundary.
The final domain status is checked again for visibility, but is still
diagnostic-only; a future gate requires a separately audited lifecycle and
end-to-end synchronization model.

## 21 new cross-module acceptance checks

- Fresh matching REST/WS pair remains ineligible.
- Mismatched/degraded/stale, disconnected and old-epoch pairs remain denied.
- Future receipt and malformed price/timestamps are rejected.
- Caller fake signature, bogus mark-update label, block timestamp and
  invalid metadata cannot authorize trust.
- Revoked/rotated clock domains fail closed.
- SQLite synthetic claims, duplicates, conflicting bindings, missing
  database and unavailable store do not authorize recommendations.
- Invalid arbitrary claim type fails closed.
- The deployed `app.assess_market_recommendation` deny-all invariant is
  evaluated offline without contacting Hyperliquid.

Run `python -m unittest discover -s tests -p 'test_*.py' -v` from the
repository root. Confirm GitHub Actions on the **actual final SHA** and
inspect the specific job log, not just a green badge.

## Stage-2 acceptance matrix

| Dimension | State | Required evidence to close |
| --- | --- | --- |
| Offline REST/WS/clock/replay integration | Automated synthetic suite | CI on exact SHA plus independent review |
| Production API recommendation gate | Deny-all, untouched | Separate deployed-SHA and live endpoint verification when authorized |
| Exchange-mark update timestamp proof | UNVERIFIED | Independently authenticated per-update mark evidence, or explicitly approve a permanently unverified design |
| Local receipt Operational Freshness | Diagnostic-only | Review the distinction from exchange provenance and age thresholds |
| Trusted clock provider | Not supplied | Dedicated authenticated implementation or retain no verified state |
| Real WS lifecycle / Render reliability | Not assessed in this PR | Separate explicitly approved bounded read-only test plan |
| SQLite multi-host / crash / tamper durability | Not established | Explicit storage threat model and architecture decision |
| Deployment / production wiring | Not performed | Separate approval and independent integration security review |
| Stage 2 completion | OPEN | Explicit user decision after review; never inferred from CI |
| Stage 3 | NOT STARTED | Forbidden until Stage 2 is fully tested and explicitly approved |

## Source-control governance

PR #20–#26 and `main` remain unchanged, this is a separate Draft PR.
A successful CI run means that tests passed for the specified research code.
It is **not** evidence of live exchange authenticity, trusted clock,
production readiness, or Stage 2 completion.

If the exchange cannot offer an independently authenticated mark-update
timestamp, the conservative supported architecture is to keep
`Verified Exchange Freshness=UNVERIFIED` while using local transport
observability strictly as Operational Freshness. Any change to the
acceptance boundary requires a new explicit approval.
