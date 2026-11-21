# ETH markPx provenance validator v1.0 (offline research only)

## Scope
Pure Python validator for Hyperliquid ETH mark observations. No REST, WebSocket,
trading, deployment or production integration. This research branch must not be
merged or connected to market endpoints without separate approval.

## Contract
- `MarketObservation`: immutable id, venue, symbol, price_type, Decimal mark,
  channel, UTC receipt time and local monotonic receipt marker.
- `ProvenanceEvidence`: same observation id, method, exchange timestamp, optional
  event/block/state reference and digest. These fields alone are **not proof**.
- `TrustedClock`: externally supplied UTC time and nonnegative uncertainty.
- `ProvenancePolicy`: positive age threshold, clock uncertainty cap, explicit
  transport health; production values are **not selected**.
- `EvidenceVerifierRegistry`: empty by default. Only a separately audited,
  independently authenticated verifier can establish that this exact ETH mark
  value belongs to a specific exchange event and its update timestamp.
- `ValidationResult`: fail-closed flags, timestamp only on success, safe reason.

## Required invariants
Missing evidence, unknown method, mismatched observation, untrusted clock,
stale mark, unhealthy transport, malformed input or verifier exception => all
three eligibility/verification flags false and exchange_timestamp null.
No REST receipt time, WS arrival time, BBO/trade/book timestamp, or block
observation time may be substituted for the mark update timestamp.
A digest or matching id is not independent provenance.
Do not carry forward a previous mark's timestamp to a new observation.

## Known limitations
No production trusted verifier is implemented or registered. The only positive
unit-test verifier is synthetic and must **never** be installed in a real
configuration. Tests verify validator logic, **not** actual Hyperliquid
timestamp provenance. The `recommendation_eligible` flag is a local result
of this isolated function and is not wired to any endpoint or trading engine.
Replay protection and full source-authentication require a future approved
verifier; a successful local synthetic test is not live verification.

## Offline verification
`python -m unittest discover -s tests -p 'test_*.py' -v`
The dedicated provenance test module contains exactly 32 offline tests.
No networking is performed.
