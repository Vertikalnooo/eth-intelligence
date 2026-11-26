# Stage 2 — Auto-Deploy recovery and acceptance evidence (2026-11-26)

**Documentation-only candidate. Stage 2 OPEN. Stage 3 NOT STARTED.**
This change records evidence and prepares a controlled automatic deployment
retest. Creating this candidate does not authorize merge or deployment.

## 1. Verified baseline before the candidate

- Repository: Vertikalnooo/eth-intelligence, main
  5e1d27bf032ebd05c4d9ff2580f890f9e5cbdd24 (merged PR #33).
- Main CI [45627840560](https://github.com/Vertikalnooo/eth-intelligence/actions/runs/45627840560),
  383/383 PASS; Python offline socket fence verified in CI. The fence is not
  an OS-wide network sandbox.
- Production service eth-intelligence-api, srv-dc001djrjlhs73fud8rg,
  branch main, checksPass, health path /health.
- Render deploy dep-dc441omc6qcc73cekg7g, exact main SHA, manual trigger
  authorized by the owner. Created 2026-11-26 14:23:30 UTC; finished
  14:24:02 UTC. Build bld-dc441omc6qcc73cekg80: 383 tests, OK, build
  succeeded. Subsequent health logs HTTP 200.
- Browser-supplied JSON and Render request log 14:25:41 UTC: GET
  /market/status HTTP 200, NO_DATA, null age, no valid cache; all denial
  flags false at both levels. The live smoke script itself was not run.
- After CI Checks Pass remains configured. The manual recovery did not
  establish that the automatic trigger works.

## 2. Bounded live REST test — 2026-11-26

The owner separately authorized one market fetch, no credentials, no
orders, no WS. The owner opened the existing production market route once.
The app's REST path uses public Hyperliquid metaAndAssetCtxs, selects the
unique ETH context and returns the perpetual mark. Screenshots and server
logs support the following observations:

| UTC | Route / evidence | Result |
| --- | --- | --- |
| 14:30:04.869 | GET /market/eth | HTTP 200; ETH/USDC perpetual mark 1699.9; local observed_at 2026-11-26T14:30:04.869194+00:00 |
| 14:30:22.096 | GET /market/status | HTTP 200; STALE, age_ms 17226, cache_valid true, cache_remaining_seconds 13 |
| 14:32:10.076 | GET /market/status | HTTP 200 in logs; body not captured |
| 14:33:09.078 | GET /market/status | HTTP 200; STALE, age_ms 184209, cache_valid false, cache_remaining_seconds 0 |

The final reason codes are transport_stale, cache_unavailable and
missing_exchange_provenance. The earlier STALE response has transport_stale
and missing_exchange_provenance. Both status snapshots preserve all six
denial flag occurrences (three at each level). Market response has
exchange_timestamp=null, exchange_freshness_verified=false,
mark_price_freshness_verified=false, freshness_status=unknown,
recommendation_eligible=false, recommendation_block_reason=missing_exchange_timestamp.

The verified scope is one successful production fetch and local receipt
aging/cache expiry. Exactly one /market/eth request is visible in the
reviewed logs; no 429/503 observed in that interval. This is not independent
upstream traffic accounting or a continuous reliability test. No additional
market calls authorized. FRESH and DEGRADED, cache-hit reuse, 429 and recovery
are offline-tested, not observed in this one-shot test. The captured price
is historical evidence, not a current quote or a trading signal.
Verified Exchange Freshness remains UNVERIFIED.

## 3. WS retirement — 2026-11-26

The owner explicitly approved retirement and deleted eth-intelligence-ws-test
(srv-dc05k9mi0phs73bnjov0) in the Render dashboard. Read-only post-deletion
verification: exact service lookup returned 404; workspace service list
contained only eth-intelligence-api, which remained live on the baseline SHA.
GitHub branch test/websocket-stability-20261120 remains at
72df9867ea436c15204630a43ad033dee892859b, merged PR #31,
[47/47 CI](https://github.com/Vertikalnooo/eth-intelligence/actions/runs/45273183989).
Both corrected startup runners are inert health-only code. The deleted
service's last historical deployed SHA was
b24d11a344c89ec0acfca8da9234c150338a91d4 (old auto-probe runner);
last deploy dep-dc1kbs60tbcc73arer7g. Do not recreate it.

Historical log at 2026-11-22 19:44:17.703227 UTC, captured before deletion:
```json
{"status":"PASS","channel":"activeAssetCtx","coin":"ETH","sessions":2,"reconnects":1,"frames_received":4,"mark_messages":2,"duration_ms":2912,"exchange_freshness":"UNVERIFIED","recommendation_eligible":false,"stage2_complete":false}
```
This is a bounded historical probe, not continuous source-health proof.
The source, PRs, CI and this evidence summary are retained. No complete
backup of deleted Render logs/configuration is claimed.

## 4. Auto-Deploy recovery retest — pending

PR #33 merged on 2026-11-25 at 13:51:22 UTC; exact-main Actions CI completed
successfully at 13:51:44 UTC. No automatic deployment appeared by the
following day. The owner showed correct repository, main branch, empty root
directory and both build filters empty, checksPass and GitHub App repository
permissions. Render check suite appeared queued on both the failed-to-trigger
commit and earlier successfully auto-deployed commit; that alone does not
establish root cause. Manual recovery succeeded as recorded above.
The underlying auto-deploy issue remains unresolved.

This candidate changes documentation only. No runtime source, dependencies,
test code, workflows, service settings or environment variables are modified.
Render previews are disabled on the production service. The ordinary Tests
workflow runs offline on push and pull_request; live research workflows have
distinct branch/path or manual triggers and must not be invoked.

Before merge:
1. Verify exact candidate HEAD and current main base, compare changed files,
   confirm both full 383-test push and PR merge-ref CI logs and socket fence.
2. Obtain separate explicit owner permission to merge and allow automatic
   CI-gated Render deployment. Documentation-only changes can redeploy.
3. Verify that current production is healthy and checksPass remains enabled.

After an authorized merge:
1. Verify merged tree matches reviewed tree and full exact-main CI succeeds.
2. Inspect Render events and deploy history: require new_commit trigger,
   deploy created after successful CI, exact merge SHA, successful build
   and live status. Manual deployment cannot satisfy the automatic trigger
   retest. Do not change settings, send deploy hooks, or force manual deploy.
3. Verify /health and /market/status via authorized read-only observations
   and logs, with full JSON contract and denial flags. Never call /market/eth.
4. If no auto-deploy appears, record failure/pending and evidence; request
   separate decision for further integration diagnosis. One successful
   retest would demonstrate the mechanism on that commit, not explain
   the earlier failure or guarantee future reliability.

## 5. Acceptance boundary

| Item | Status at baseline |
| --- | --- |
| PR #33 production acceptance | PASS, manual deployment |
| Bounded live REST and stale/cache expiry | PASS within observed scope |
| WS retirement | Confirmed; branch/source retained |
| Auto-Deploy retest | PENDING |
| Verified exchange mark-update freshness | UNVERIFIED |
| Continuous WS / REST source reliability | Not established |
| Distributed durable replay | Not deployed; offline synthetic only |
| Recommendation eligibility | false |
| Stage 2 | OPEN, explicit closure decision required |
| Stage 3 | NOT STARTED |

No new exchange access or trading authorized. No service deletion, creation,
configuration change, deployment or Stage 3 work is authorized by this PR.
