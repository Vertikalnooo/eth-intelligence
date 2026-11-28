# Stage 2 — REST One-Shot Final Hardening

Research only. Stage 2 OPEN; Stage 3 NOT STARTED. No live grants, requests, WS,
trading, merge, deployment, service or workflow changes authorized.

## Implemented research design

The old public unsupervised run_one_shot now always denies; its private
connection-injected regression fixture exists only to retain offline coverage.
The new hardened public executor accepts only an OFFLINE scope and fixed
loopback worker. There is no live grant issuer or live worker; app.py and the
current production /market/eth behavior are unchanged. No startup I/O.

- Explicit offline store initialization uses exclusive creation, private
  directory/file ownership/mode, canonical path, no symlinks/hardlinks,
  pinned device/inode and store UUID, checked sidecars, SQLite DELETE
  journal with FULL synchronization. Persist the identity anchor externally
  in any future trusted deployment; never derive a replacement anchor
  automatically from the DB after restart.
- Explicit offline HMAC-SHA256 grant issuance (32-byte operator key),
  opaque unique ID, scope, store ID, candidate SHA, issue/expiry, TTL <= 300s.
  No secrets in repo or real grants created by CI. Atomic ISSUED -> CONSUMED
  or REVOKED, terminal states, no normal reset/delete, consumption before
  worker launch. Unknown/expired/forged grants fail closed.
- Separate Linux subprocess fixed to 127.0.0.1, one POST with the existing
  strict HTTP/body parser, no redirects/retries. Parent measures a monotonic
  8s maximum transport budget before launch, waits only for remaining
  budget, rejects late results, kills/reaps on timeout. Child arms an
  independent kernel SIGALRM timer and PR_SET_PDEATHSIG (SIGKILL), rechecks
  parent PID before I/O. Worker has no descendants. No retry on timeout,
  crash, lost reply or unknown network outcome.
- Actual counted loopback POST tests include stuck response headers/body,
  disconnect, redirect, parent SIGKILL during HTTP and process exit after
  durable consumption before worker creation. No external socket needed.
  Existing legacy local lifecycle tests retained; new worker's PASS is
  OFFLINE_ONLY and is not an authenticated exchange receipt or production
  cache measurement.

## Trust and timing limitations

The HMAC issuer/verifier and DB writer are within one trusted operator/OS
boundary. HMAC is not separation of duties; an actor with the key can sign
grants, and an actor with DB write access can bypass SQLite triggers, restore
bytes in place or alter the schema. Path/inode/UUID checks reject accidental
replacement/copied files when the anchor is retained, not an adversarial
same-inode rollback or privileged TOCTOU. No claim of distributed exactly-once,
tamper-proof ledger, rollback resistance or trustworthy external wall clock.
A new grant is never a recovery mechanism for a prior uncertain request.
Clock expiry relies on trusted local wall time. A reservation records at most
one client attempt; it does not prove that the exchange received a request.

The transport budget covers worker launch, DNS/TLS if implemented in a future
worker, HTTP, parsing and child exit in the supervised model; the current
worker uses loopback HTTP (no live DNS/TLS). It begins after durable grant
consumption. OS scheduling, process creation and uninterruptible kernel
operations can delay the supervising caller; bounded cleanup is separate.
Do not describe this as hard real-time or independent upstream accounting.
Linux parent-death signals track the creating thread, so future invocation
must use a stable dedicated single-thread supervisor. Worker result pipe is
trusted local code, not an adversarial input channel. No OS-level network
sandbox is claimed; Python CI socket fence is retained.

## Production integration proposal (separate approval)

1. Keep PR draft until exact head push and merge-ref offline CI and review.
   Verify the deployed main SHA, previews disabled and no live triggers.
2. Decide whether production market access should be disabled by default
   globally or separated into a private, owner-controlled diagnostic worker.
   A public /market/eth request must never bypass the one-shot budget.
   Preserve /health and /market/status as local read-only routes.
3. Select durable, single authoritative storage and a protected externally
   pinned identity and non-rollback authorization record. Render free
   service filesystem is not a demonstrated durable trust boundary.
   Prefer a transactional external store with unique operation ID,
   append-only terminal events and independently managed issuer identity;
   do not provision any service or key in this PR. Review recovery,
   backup/restore, multi-instance and clock trust explicitly.
4. Design owner-authenticated grant issuance and a fixed live worker:
   no public issuer, no grant in URL/logs, no automatic issuance, exact
   deployment/operation binding, explicit live scope, TLS hostname
   verification, fixed Hyperliquid public info POST, no proxy/redirect/retry.
   Supervise with the tested process architecture and separately validate
   DNS/TLS timeouts offline. Do not activate from startup, CI or health.
5. Define a genuine production receipt/cache integration test: one
   authorized worker attempt, capture receipt in the receiving process's
   clock domain, sample actual cache under the same lock, no refetch.
   Preserve deny-only flags and reason codes; missed windows fail report.
   Existing 2 MiB body cap not live-validated.
6. Separate implementation approval, full exact CI, independent review;
   separate merge + automatic CI-gated deploy approval; verify exact
   production SHA and local health/status without market request.
7. Separate explicit single live authorization only after all prior gates
   pass. One reservation, no retries or follow-ups to the exchange;
   if outcome unknown stop. No Stage 2 closure without owner decision.

This candidate improves the research safety primitives. Production integration,
actual durable/rollback-resistant store, live DNS/TLS validation and genuine
production cache lifecycle remain blockers. Exchange freshness UNVERIFIED;
recommendation_eligible=false; stage2_complete=false.
