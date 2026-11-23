# Stage 2 — WS test-service startup and restart safety (2026-11-24)

**Draft-only remediation. No test service restart, deploy, network connection,
production change, merge or Stage 3 authorization.**

## Target and exact baseline

- Repository: Vertikalnooo/eth-intelligence.
- Target branch: test/websocket-stability-20261120, original
  SHA b24d11a344c89ec0acfca8da9234c150338a91d4.
- Proposed source is on a separate fix branch; no direct write to the
  target branch or to main.
- Render eth-intelligence-ws-test is SUSPENDED by the user and configured
  autoDeployTrigger=commit on that target branch. It MUST remain suspended.
  A future merge into the target branch can trigger a deployment event,
  irrespective of the suspended flag. Do not merge without a separately
  approved operational plan.
- Production eth-intelligence-api is on main and must not be changed here.

## Remediation

Both historical Render entrypoints, render_ws_stability_runner.py and
render_ws_probe_runner.py, used to launch an exchange WS child probe
automatically on interpreter startup. The first depended on an ephemeral
/tmp marker. Container replacement, instance restart or manual test-branch
redeployment could discard the marker and repeat a previously authorized
single-use probe without fresh approval.

The new entrypoints are HTTP liveness-only. They cannot spawn a probe,
subscribe, reconnect or call Hyperliquid from service startup, even with
historical opt-in environment variables left configured. No temporary
filesystem lock and no persistent approval flag are used. The legacy
entrypoint delegates to the inert implementation.

GET /health reports HTTP 200 for the local HTTP process only and includes
probe_state=disabled, reason_code=manual_probe_requires_separate_approval,
exchange_freshness=UNVERIFIED, source_reliability_verified=false,
recommendation_eligible=false and stage2_complete=false.
It MUST NOT be interpreted as exchange connectivity, source freshness,
mark timestamp provenance or a successful WS sampling test.

The explicit probe_hyperliquid_ws_stability.py remains unchanged and
technically executable by an operator; it is **not** called from either
entrypoint. Any separate manual execution requires new, narrowly bounded,
explicit human approval. This PR does not implement or authorize such a
manual run. Do NOT resume the suspended Render service to test it.

## Offline verification

- Synthetic startup tests execute both entrypoints three times with mock
  HTTP servers, prohibited subprocess/spawn and socket connections,
  and mocked background-thread launch. No network or Render changes.
- Health diagnostics and unrecognized routes are tested; permissive env
  flags never activate the probe or recommendation eligibility.
- Workflow now fails closed if Python offline socket fence is absent.
  The guard disallows external TCP/UDP within Python, not all OS/network
  mechanisms or separate native binaries; CI is not a complete sandbox.
- Require successful push and pull_request GitHub Actions on exact final
  commit, full test counts, no unexpected source modifications, and
  independent review before considering merge.

## Out of scope and release gate

- No changes to production app.py, Render settings, accounts, tokens,
  private channels, orders, trading, exchange mark timestamp proof, or
  Stage 2 conclusion.
- Do not merge this Draft PR into auto-deploying test branch until a
  *separate explicit user decision* on deploying an inert health-only
  process to a currently suspended service. Existing deployment must not
  be resumed for acceptance.
- Production operational-freshness API wiring is a separate Stage 2 item.
- Stage 2 OPEN; Stage 3 NOT STARTED.
