# Stage 2 Safe Production Baseline — research candidate
Base: main b5f654058305aac697e07caa6b454085113f50d3.
Stage 2 OPEN; no merge, deploy, exchange calls, WS or trading.

## Behavior
Existing Render startup command python app.py remains unchanged. app.py has
no upstream transport, endpoint URL, mutable market cache, live feature
flag or import of the historical fixture. GET / and /health: 200, version
0.2.2, mode SAFE_BASELINE. GET /market/status: existing exact top-level and
REST keys, state disabled, throttle_reason upstream_disabled, no cache,
NO_DATA, all three deny flags false. GET /market/eth: 503, no price and
explicit upstream_disabled reason. Unsupported write methods remain 501.
No background work, no live client, no automatic recovery or authorization.
The former REST/cache/backoff implementation is retained only as a historical
offline test fixture (tests/legacy_market_fixture.py), with no default fetch
transport or startup entrypoint. Tests that exercise old behavior explicitly
target that fixture; they are no longer evidence of live production behavior.
Production release boundary and recommendation tests remain, and new tests
exercise the actual app.py, including the actual process startup. The existing
read-only smoke supports strict --require-disabled checking; no live smoke
run is authorized in this change. Existing historical research scripts and
their branch-scoped workflows are unchanged, never invoked by app.py or CI.

## CI and rollback
Full suite in Linux network namespace with loopback only and Python offline
socket fence. No dependency installation or new services. GitHub checkout
and setup occur before isolation. Render build command remains unchanged;
no external services are needed by tests.
After separately approved merge/deploy: verify exact main CI, Render exact
commit, /health and /market/status only, strict disabled state, old instance
retired. Render zero-downtime transition may briefly retain old instance;
do not claim protection until verified. Never run /market/eth on old
production. Later rollbacks must target this verified disabled revision
with python app.py, never prior commits. This candidate does not implement
a Render control-plane rollback restriction; privileged operators must
enforce the runbook. No changes to current main, PR #35, PR #36 or Render.
Later integration must reconcile stacked PR conflicts and adapt old app
import expectations without silently reinstating upstream access.
