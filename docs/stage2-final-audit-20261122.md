# Stage 2 research Release Candidate — final audit and handoff

**Status:** CANDIDATE FOR FINAL USER REVIEW, **NOT APPROVED**.
Stage 2 remains OPEN; Stage 3 NOT STARTED. This document is intentionally
separate from any production deployment authorization.

## Scope and source lineage

- Repository: `Vertikalnooo/eth-intelligence`.
- Existing production `main`: `b86fe378e7c0fbf19044a24a17c552bbe63b319a`.
- Exact research baseline: PR #29 `327dabc52347ea122ce8ff101c255e61319ad6ce`.
- This candidate extends Draft PR #30 only; the full chain PR #21–#29 is
  inherited and the #20 provenance snapshot is retained with later fixes.
- No merge, production edit, service config change, exchange access, order,
  trade, outbound WS subscription or Stage 3 work was performed.

## Architecture and invariants

- **Operational Freshness** is a caller-supplied monotonic receipt-age
  diagnostic, with REST request timing, WS session and epoch, clock-domain
  lifecycle and REST/WS comparison. It does NOT measure authenticated
  exchange mark-update age.
- **Verified Exchange Freshness** stays `UNVERIFIED` for every unified
  Stage 2 report. Missing/malformed data has independent `INVALID`
  and reason-code diagnostics, never a fabricated `VERIFIED` status.
- Existing `app.eth_market(fetch=memory)` and SDK-shaped
  `activeAssetCtx` fixtures are mapped into typed research observations
  via strict offline adapters. Neither adapter assigns an exchange event
  timestamp or accepts a claimed event ID, forged proof or caller flags.
- The unified report performs no network or SQLite I/O and yields
  `accepted=False`, `recommendation_eligible=False`,
  `stage2_complete=False`, `replay_result=UNAVAILABLE`.
  The separately tested synthetic SQLite replay primitive does not
  authorize anything.
- Local clock-domain identity, revoke, rotate and fork resets are tested.
  This is process-local lifecycle consistency, not a trusted clock.

## Tested conditions

The full unittest suite includes historical API/fail-closed coverage and
new negative tests for clock-domain clones, immutable report denial
flags, real API JSON fixtures, SDK WS envelope/epoch/disconnect,
REST/WS mismatch/stale/degraded, future monotonic receipts,
Decimal/market/type validation and adversarial Python mapping keys.
GitHub Actions has a Python startup network fence that blocks external
socket connect/connect_ex/sendto while allowing loopback fixtures.
The full test suite runs on GitHub Python 3.12 with the fence activated.

**Proof requirements:** verify both final SHA push and PR merge-ref
workflows report success, inspect job logs for `Ran N tests`, `OK`
and `Offline CI: external TCP/UDP blocked; loopback allowed`. Do not
trust CI on an intermediate SHA. CI does not exercise external binaries
or native socket bypasses and is not a complete process sandbox.

## Read-only Render evidence (2026-11-22)

The connected Render workspace was inspected **without any write**.

| Component | Read-only evidence | Implication |
| --- | --- | --- |
| `eth-intelligence-api` | Latest live deployment `dep-dc0dl8mq1p3s73bklek0`, SHA `b86fe378...`; branch `main`; build command `python -m unittest discover -s tests -v` | RC research code is NOT deployed; current production remains isolated |
| API deployment policy | `autoDeployTrigger=commit` | CI-before-deploy is **not guaranteed**. Must be corrected or explicitly accepted before any release |
| API health | Available application logs from 2026-11-22 18:50Z: `HEAD /health 200`, `GET /health 200`, `GET /market/status 200` | Historical read-only request evidence, not newly probed current availability or mark provenance |
| `eth-intelligence-ws-test` | Latest live service deployment `dep-dc067ce7bikc739ghhbg`, SHA `3e67f324...`, branch `test/websocket-stability-20261120` | Historical isolated WS test remains independent. It is not authenticated ETH mark lifecycle verification |

No production URL was fetched, no Hyperliquid API was called, no
production endpoint or Render service settings were changed.

## Audit findings and decision gates

| Priority | Finding | Disposition |
| --- | --- | --- |
| P1 — external release gate | `autoDeployTrigger=commit` on production means deployment could precede CI success | **OPEN**; production config change prohibited in this candidate |
| P1 — runtime acceptance | No authorized current end-to-end test of deployment, ETH mark lifecycle, reconnect resilience or exchange-source latency | **OPEN**; offline pass is not a substitute |
| P1 — proof boundary | No independently authenticated source timestamp bound to `markPx` or trusted real clock | **UNVERIFIED BY DESIGN**; Stage 2 operational-only scope must be explicitly accepted; recommendation gate stays denied |
| P2 — storage | SQLite synthetic replay has no approved multi-host, tamper and retention policy | **OPEN**; not used by unified offline report |
| P2 — transport | REST/WS API contracts have test-fixture verification only; no live source subscription in this candidate | **OFFLINE PASS**, live blocked |
| P2 — CI | Local socket guard covers Python socket paths but is not full machine network isolation | **LIMITATION DOCUMENTED**; external egress prohibited by project governance |
| Remediated | Clock-domain copy, input comparison side effects, mutable acceptance flags, malformed source binding, unified replay under lock | **TESTED** |
| Governance | Independent final audit and explicit user decision on Stage 2 | **PENDING** |

**Approval needed to close Stage 2:** independently review final SHA,
full CI logs and the accepted scope. Resolve production release governance
and live reliability via separately authorized non-trading work if these
remain in Stage 2 acceptance scope. No automatic merge or Stage 3 transition.
