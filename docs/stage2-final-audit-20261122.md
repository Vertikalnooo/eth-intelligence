# Stage 2 research Release Candidate — final audit and handoff

> Historical snapshot. For the verified 2026-11-25 production baseline and
> remaining acceptance items, see [Stage 2 Final Hardening](stage2-final-hardening-20261125.md).
> Earlier SHA, Draft/merge and deployment statements below refer to their original review.

> **Update 2026-11-23: later evidence supersedes the historical Render
> snapshots and open approvals in this document.**
> The owner approved the operational-only, read-only architecture, with
> Verified Exchange Freshness permanently UNVERIFIED and every recommendation
> gate denied. Render production now uses checksPass and /health. Its
> health-path setting caused one successful SAME-SHA deployment
> dep-dc1l9pec6qcc73b5qqeg on main b86fe378e7c0fbf19044a24a17c552bbe63b319a.
> The isolated bounded ETH activeAssetCtx test passed one controlled
> reconnect; the user then suspended eth-intelligence-ws-test at
> 2026-11-22T21:07:00Z. Do not resume it: its ephemeral /tmp one-shot
> lock is not restart-safe. PR #30 remains Draft, unmerged, offline-only
> and not wired into production. No merge, deploy or Stage 3 approval
> was granted. See stage2-integration-release-plan-20261123.md.
>


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

## Release blockers: operational-only acceptance and Render execution plan

As of the latest read-only inspection, production Render uses
`autoDeployTrigger=commit` and `healthCheckPath=""`. According to official
Render documentation, **After CI Checks Pass** corresponds to
`autoDeployTrigger=checksPass`. The existing manually configured service is
not controlled by a Blueprint in this branch, so changing repository YAML
would **not** change the live service. Required operator action:

1. In Render > `eth-intelligence-api` > Settings, set **Auto-Deploy** to
   **After CI Checks Pass** (not On Commit), and verify the saved value.
2. Set **Health Check Path** to `/health`, if supported by that service,
   and validate without forcing a new deploy.
3. Do **not** switch the branch away from `main`, do not merge PR #30,
   and do not deploy the candidate as part of this settings change.
4. Read back the Render configuration; verify that a failing CI commit
   is not deployed through a controlled non-production exercise (never push
   an intentionally failing commit to `main`).
5. Preserve current deployed SHA `b86fe378...` as rollback reference.
   If settings changes unexpectedly trigger a deploy, abort and report.

The connected Render tool does not expose a direct service-settings update
action. Therefore the settings correction is **not yet performed**; it needs
an authorized dashboard/browser action and read-back.

### Repeatable bounded API diagnostics

The new `scripts/stage2_render_readonly_smoke.py` is **plan-only by
default**. Live mode additionally requires the environment variable
`STAGE2_LIVE_READONLY_APPROVED=yes`. It allows exactly two GETs on
`https://eth-intelligence-api.onrender.com`: `/health` and
`/market/status`, 5 s per request and at most 8 KiB per response.
Redirects and wrong hosts are refused. It never calls `/market/eth`,
a Hyperliquid endpoint, WebSocket or any trading route.

It returns HTTP and schema success, with
`source_reliability_verified=False`, `exchange_mark_verified=False`,
`stage2_complete=False` regardless of a successful run.
Offline tests use injected memory responses and deliberately do not run
live mode. A read-only smoke pass is **not** a production stability test.

### Separate bounded WS operational verification, not yet executed

For future explicit approval: use a disposable isolated research runner;
subscribe only to ETH `activeAssetCtx`, no authentication or private
channels, hard duration cap 60 s, hard message cap 30, one reconnect at most,
client-side timeouts, immediate stop on unexpected channels/schema,
no raw payload/price disclosure, reason-code/latency summary only.
Do not reuse the historic one-time 30-second probe authorization.

A pass would establish **sampled transport operations**, not authenticated
exchange update timestamps or durable uptime. Before claiming Stage 2
operational completion, inspect the runner's logs and deployed SHA,
document a rollback/stop procedure and compare with the offline
acceptance matrix. The live exchange remains outside this commit.

### Scope decision needed for Stage 2 closure

The recommended acceptance target is **operational-only read-only market
observation** with `Verified Exchange Freshness=UNVERIFIED`.
Local FRESH is never an exchange-proven time. Recommendation eligibility,
order execution and all trading remain denied. This target cannot be
implicitly accepted by a green CI run; it requires the user's explicit
architectural signoff and an independent final review.
