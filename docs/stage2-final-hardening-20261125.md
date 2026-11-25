# Stage 2 Final Hardening — 2026-11-25

**DRAFT candidate. Stage 2 OPEN. Stage 3 NOT STARTED.**

## Verified starting point

- main and deployed production: 038c7b47f07321067911bc1cf6abc50ab162b2e0.
- PR #32 merged on 2026-11-25. Main push CI 45610252291: 369/369,
  Python external TCP/UDP fence active (loopback permitted). Render build
  369/369; dep-dc3ctk68bjmc73bnf9ag live at 12:05:11 UTC. Build started
  after CI success; autoDeployTrigger=checksPass, health path /health.
- User-supplied browser JSON at 15:08 MSK and corresponding Render log
  at 12:08:47 UTC: GET /market/status 200, state ready_to_retry,
  cache_valid=false, rest_operational_freshness.state=NO_DATA,
  age_ms=null, local monotonic clock, REST channel, no_rest_receipt
  and missing_exchange_provenance. All three denial flags false at both
  levels. Other receipt states verified offline, not with new live data.
- WS PR #31 merged to test/websocket-stability-20261120:
  72df9867ea436c15204630a43ad033dee892859b. Render service
  eth-intelligence-ws-test is user-suspended. Last deployed SHA
  b24d11a344c89ec0acfca8da9234c150338a91d4 still auto-runs a probe
  at startup. DO NOT resume; a historical deployment labeled live is not
  evidence of a running suspended service. The inert runner is not deployed.

## Candidate changes

Only smoke verification, offline tests and documentation. No app.py,
rest_operational.py, research evaluator, workflow, WS branch or Render
configuration changes. No additional upstream I/O. No invocation of the live
smoke script for candidate verification.

The verifier requires the exact current 13 top-level status keys and 8
nested REST diagnostic keys. A future schema change needs review rather
than silently passing. Exact Boolean false at both levels for
exchange_freshness_verified, recommendation_eligible and stage2_complete.
Checks include integer types (not bool), nonnegative age and counters,
cache/throttle consistency, source/clock/channel, five receipt states,
known unique reason codes and missing_exchange_provenance.

Age is floor(elapsed_ns/1e6). The producer compares nanoseconds: DEGRADED
can display 2000 ms and STALE 5000 ms, so the validator allows those
boundary overlaps without treating local age as exchange evidence.
Seconds counters also round; zero at a transition is not proof of no
throttle. INVALID can retain a local age when a cache became unavailable.
No requirement for a receipt on a clean startup.

Pre-release audit follow-up: INVALID with a known age must represent a lost
cache and the corresponding fresh/degraded age band; null-age capture and
assessment errors retain their distinct cache semantics. Active 429 cooldown
requires NO_DATA and the exact cooldown reason set. Expiry alone does not
restore a receipt while the consecutive 429 counter remains positive. A real
app lifecycle regression uses a mocked upstream and monotonic clock to verify
429, expiry and successful recovery. No production producer changes.

The smoke remains exactly two allowed GET routes, with no redirects,
query parameters, alternate host or port; existing 5s per-request timeout
and 8192-byte response cap retained. Duplicate JSON keys, non-finite
JSON constants, malformed UTF-8/JSON and excessive JSON nesting fail closed.
The timeout is not a total wall-clock deadline. This is diagnostic validation,
not a general network sandbox or authenticated source-health test.

Fourteen additional offline test methods cover all states, boundary ages,
missing/extra keys, malformed or forged metadata, inconsistent state and
throttle fields, ambiguous JSON, URL allowlist, failure propagation and the
actual app status producer with a mocked upstream. Existing nine smoke
tests retained/updated for the new schema. Require full exact-HEAD push
and PR merge-ref CI and inspect raw logs before considering merge.

## Remaining acceptance items

| Priority | Item | Disposition |
| --- | --- | --- |
| P1 | Old suspended WS deployment | Recommend deliberate retirement after separate user approval, retaining source/evidence. Do not resume, delete, reconfigure or deploy as part of this PR. |
| P2 | Automated smoke schema | Candidate; requires CI and review. |
| P2 | Current documentation | Candidate; prior dated reviews retained as history. |
| Scope | Authenticated exchange mark timestamp/trusted clock | UNVERIFIED by design; no trusted provider. |
| Scope | Continuous live ingestion and WS reliability | Not proven; historical bounded probe is not uptime evidence. |
| Scope | Durable distributed replay | Not deployed; temporary SQLite synthetic tests only. Unified offline evaluator never writes replay. |
| Gate | Stage 2 closure | Separate explicit decision, after high-priority issues resolved or explicitly scoped out. Stage 3 prohibited. |

No recommendation or trade authorization follows from diagnostics PASS.
Any real exchange test requires separate narrowly bounded approval.
A subsequent main merge can trigger a CI-gated production redeploy even
for documentation/test-only changes. Separate approval is mandatory.
