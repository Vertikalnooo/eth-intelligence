# Operational Freshness vs Verified Exchange Freshness

**Status:** Stage 2 offline research contract, not production. This branch is
based on Draft PR #25 HEAD `3f720f71f82d804f2fab8962d2fd15222fa23345`.
No exchange calls, orders, source subscriptions or endpoint modifications.

## Distinct assurances

| Dimension | Grounding | Allowed diagnostic status | Not established |
|---|---|---|---|
| **Operational Freshness** | Local monotonic receipt age, per-process clock domain, REST timing order, WS connected/epoch state | FRESH / DEGRADED / STALE / DISCONNECTED / INVALID | Authentic age of the exchange's underlying mark update |
| **REST/WS Consistency** | Two validated local observations, window and bps threshold | CONSISTENT / MISMATCH / NOT_COMPARABLE / INVALID_INPUT / DISCONNECTED / STALE | Independent issuer proof or trustworthy timestamp |
| **Verified Exchange Freshness** | Would require trusted exchange-origin event, exact mark binding and independently trusted UTC | UNVERIFIED or INVALID_EVIDENCE today | VERIFIED_FRESH is not supported by a real verifier |
| **Trust-claim research** | Caller-supplied event ID, UTC time, semantic label, opaque evidence | UNVERIFIED / INVALID | Cannot produce VERIFIED or `accepted` |

**Operational FRESH means only that the application received the observation
recently on its local clock. It is never proof that the mark price was recently
updated by Hyperliquid.** Agreements across REST/WS are not proof either.

## Validation and reason-code precedence

`trust_research.inspect_mark_update_claim()` first checks the type of the
claim and **all** supplied metadata fields (event ID, claimed UTC time and
timestamp semantic). If any metadata is malformed, it returns `INVALID`
with every applicable metadata reason code even when the REST/WS transport is
disconnected or stale.

With valid metadata, `assess_transport()` checks the local receipt timing and
WS session. Transport `INVALID` gives claim `INVALID`; `DISCONNECTED`,
`STALE`, `DEGRADED` or `FRESH` all give claim `UNVERIFIED` and
retain diagnostic reasons. Unknown evidence is opaque and treated as
**untrusted, not authenticated**.

Transport age thresholds (fresh <= 2000 ms; stale > 5000 ms) remain
provisional research settings, not proven exchange service-level guarantees.
No legacy `compare_rest_ws()` result is an admission gate.

## Downstream contract

- No value, status or boolean from these offline diagnostics can authorize a
  recommendation, trading action, market-data admission or promotion to
  `Verified Exchange Freshness`.
- `exchange_provenance_verified=False` and
  `recommendation_eligible=False`; `accept_market_data().accepted=False`
  and `combined_deny=False`.
- Future work must make an explicit architectural decision: either source
  independently verifiable ETH mark-update evidence with documented binding
  and trusted clock, **or** retain the distinction and never claim
  exchange-verified timestamp freshness. No fallbacks that forge timestamps.
- A separate independent audit and user's explicit Stage 2 approval are
  required before any wider integration; Stage 3 remains blocked.

## Tests

`tests/test_trust_claim_validation_order.py` adds ten negative tests for
stale/disconnected malformed claims, multiple simultaneous metadata errors,
old WS epochs, degraded data and forged evidence. Run full offline suite with
`python -m unittest discover -s tests -v` and verify exact-HEAD GitHub CI.
