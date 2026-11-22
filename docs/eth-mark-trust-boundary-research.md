# ETH mark-update source trust architecture — offline research contract

**Scope:** Stage 2 research, Draft only. Branch originates from PR #24 HEAD
`458f9c2d72963b481b359986ab7853c553521b6d`. This document is a design,
not a deployed data source or a claim that Hyperliquid mark updates are authenticated.

## Evidence available from public documentation

- Hyperliquid websocket `activeAssetCtx` subscription exposes a per-asset
  `ctx.markPx`. Official subscription documentation does **not establish**
  a cryptographically authenticated event timestamp uniquely bound to that
  specific mark price update:
  https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
- Hyperliquid's SDK `metaAndAssetCtxs` specification exposes `markPx` in
  REST context but does **not establish** an independently verifiable per-update
  exchange timestamp:
  https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/api/info/assetctxs.yaml
- `activeAssetCtx`, `metaAndAssetCtxs`, `fastAssetCtxs`, `allMids`,
  trade timestamps, candles, orderbook timestamps, block timestamps and
  application receipt times must not be silently equated to an authenticated
  timestamp of the ETH mark update. Exact source payload semantics must be
  separately confirmed against current authoritative documentation before
  future integrations. This offline PR does not call any endpoint.

## Threat model and required evidence

| Threat | Why local checks cannot close it | Required future independent control |
| --- | --- | --- |
| Caller forges a `mark_update` label, event ID or timestamp | Dataclass validation checks structure, not issuer | Authenticated origin; timestamp bound to exact event, price and instrument |
| Replay of valid-looking prior mark update | SQLite protects only keys actually claimed | Exchange-defined, verifiable event identity, namespace and replay lifetime |
| Receipt clock/transport time misrepresented as exchange time | Monotonic age starts at receipt | Distinct authenticated update time and trusted, uncertainty-bounded clock |
| Correct REST/WS price comparison mistaken for origin proof | Two observations may share upstream contamination | Independent evidence provider/verifier, not mere channel agreement |
| WS disconnect/reconnect and stale snapshots | Current session object is caller-supplied | Audited lifecycle, epochs, health checks, invalidation and gap policy |
| Source adapter replaced or endpoint compromised | TLS protects transport to one endpoint, not mark event semantics | Pin/verify source identity, schema, transport integrity, signed evidence if available |
| SQLite file replaced, process restarted or split-brain storage | Local database does not assure cross-host or filesystem authenticity | Durable shared storage contract, ACLs, migration and crash testing |
| Synthetic fixtures report success | Test values can be forged intentionally | Strictly separate offline diagnostics from production acceptance |

## Proposed trust boundary (no implementation authorized)

1. **Receive:** capture raw source bytes, source identity, schema version,
   transport lifecycle and local monotonic receipt marker. Do not derive or
   synthesize a mark-update timestamp.
2. **Authenticate:** a future trusted provider must verify actual evidence
   issued by a trustworthy source and a canonical binding across venue,
   market, mark price, event ID, exchange update timestamp and semantics.
   If no authoritative per-mark-update proof exists, return UNVERIFIED.
3. **Clock:** independently validated UTC provider with measured uncertainty,
   monotonic sampling domain, source health, expiration and rollback detection.
   Name strings and injected provider callbacks do not grant trust.
4. **Lifecycle:** WS session epoch and transport receipt freshness must be
   evaluated separately from exchange-mark freshness. REST/WS consistency
   remains optional, diagnostic-only, and never upgrades trust.
5. **Replay:** only after authentication and binding validation may a
   separately audited stateful gate atomically consume a canonical event key;
   define multi-process/multi-host, retention, recovery and conflict semantics.
6. **Admission:** **not part of this PR**. A future gate requires an explicit
   new approval and independent audit, and must fail closed on any missing,
   stale, inconsistent, unavailable or unverifiable input. Risk limits and
   order execution are separate decisions and remain out of scope.

## Executable research boundary in this PR

`trust_research.inspect_mark_update_claim()` accepts **untrusted** claim
metadata solely to produce `INVALID` or `UNVERIFIED` with reason codes.
It does not expose `VERIFIED`, an `accepted` field, or any live provider.
`exchange_provenance_verified` and `recommendation_eligible` are always
False. An assertion named `mark_update`, a caller UTC time, caller event ID,
or opaque byte signature is never trusted. Malformed input, stale receipt,
disconnect, old epochs and wrong domains are distinguished without upgrading
any result.

18 adversarial tests include synthetic evidence, unrelated timestamp types,
missing WS session, reconnect epoch, missing/wrong clock domain, stale or
degraded transport, NaN price, and fail-closed assertions.

## Decision gates / stage-2 acceptance conditions

- [ ] Independently verify whether authoritative Hyperliquid ETH mark-update
      event and per-update timestamp can actually be authenticated. If not,
      keep exchange mark freshness UNVERIFIED and do not enable a gate.
- [ ] Specify a real non-injectable evidence trust anchor or formally conclude
      that such a trust anchor is unavailable for this data feed.
- [ ] Specify trusted clock operation, calibrated uncertainty and restart/fork
      behavior beyond untrusted local source labels.
- [ ] Design and separately audit live source lifecycle and transport health.
- [ ] Define shared durable replay policy, authoritative key, migrations,
      storage reliability, disaster recovery and tamper boundaries.
- [ ] Review any future production admission gate and acceptance criteria in
      a separate PR. **No production allow path may be enabled in Stage 2.**
- [ ] Pass comprehensive offline regression and independent security review.
- [ ] Obtain explicit user approval to close Stage 2; no automatic Stage 3.

**Invariant:** `accept_market_data().accepted=False`,
`recommendation_eligible=False`, `combined_deny=False`. No exchange
connections, REST/WS sessions, subscriptions, orders, endpoint changes,
production modifications or trading. Source PRs #20-#24 and main are untouched.
