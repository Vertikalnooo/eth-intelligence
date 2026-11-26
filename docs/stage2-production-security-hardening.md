# Stage 2 Production Security Hardening — offline candidate

Base: PR #36 previous head f786874d4b6261324572748941096800930ea22b.
Stage 2 OPEN; Stage 3 NOT STARTED. No merge, deploy, Neon, live requests or trading.
Existing app.py, Render and workflows are unchanged.

## Research additions
- LocalPg is a Psycopg 3 interface with fixed loopback TLS target, parameterized
  SQL, verify-full and required channel binding, explicit commit acknowledgement,
  no retry. No remote DSN, no actual Neon or live credentials. The checked-in
  research dependency manifest pins direct dependencies only; no installation
  in offline CI. Adapter contract is tested with a mock; actual PostgreSQL
  SQL tests use the previous local psql fixture. Actual Psycopg TLS/SCRAM and
  dependency build/hash-lock verification remain mandatory before production.
- ei2 schema serializes every transition on a single row. Four disjoint
  execute-only roles (recovery, issuer, executor, finalizer), fixed search
  path and no general transition/table rights. State is DISARMED -> ARMED ->
  PENDING -> CLOSED/FROZEN. A fresh process epoch is mandatory for ARM.
  A single grant per arm. Conservative 900s cooldown after terminal result.
  No automatic recovery or administrative reset function.
- Fence starts DISARMED, with external anchor (store UUID, checkpoint, SHA)
  supplied by trusted operator, not read automatically from the database.
  Every committed transition advances the checkpoint. Failed/unknown commit
  freezes local state. CONSUME acknowledged before one supervised local TLS
  attempt; SUCCESS acknowledged before returning data. External anchor may
  be advanced only after separately reconciled terminal evidence. Stale anchor
  rejects restart even if SUCCESS was committed but ack lost. Pending or
  frozen ledger is blocked even with a current checkpoint.
- Library-backed registration and authentication ceremonies (localhost only):
  one-use 32-byte bootstrap token, one pending challenge, UV required, exact
  RP/origin, pinned credential, operation/epoch/anchor binding, replay deny.
  Enrollment yields pin for trusted operator to provision separately; never
  authenticates in the registration process. Verification uses py_webauthn
  APIs; offline contract tests use mock library. No real iPhone enrollment or
  external dependency install. The prior PR #36 ES256 actual signature tests
  remain. Persistent enrollment disable and sign-count pin updates still need
  an explicit secure provisioning workflow. No browser routes/UI added.
- Both research TLS/HTTP subprocesses get a minimal fixed environment, including
  the existing Python loopback socket guard; no parent DB credentials/tokens.
  Inherits no extra file descriptors. The child is local-only.
- safe_rollback_api.py is a standalone disabled-mode entrypoint, no app.py
  import or outbound route. Health/status public, market 503, admin POST 403.
  Do not deploy old app.py/main as rollback; the existing deployed version
  still has a public market fetch. Future deployment must explicitly switch
  startup command and retain a tested disabled baseline.

## Acceptance limitations
The new fence and ceremony are offline research components, not mounted in the
existing PR #36 candidate handler or app.py. New worker returns a validated
market snapshot; existing cache lifecycle is not wired to the new fence.
Runtime roles prevent accidental cross-operation SQL calls, not a compromise
of a process possessing all role credentials. Production separation requires
separate credentials and trust boundaries; no live role provisioning yet.
The independent anchor is a manually supplied trust input, not yet implemented
as a durable external registry. A correlated rollback of both DB and external
anchor or privileged actor is outside the guarantee. Never auto-adopt a DB
checkpoint; after any ambiguous transition, freeze until explicit review.
A restore to an earlier state that matches a stale external anchor cannot be
detected by equality alone; live operation requires an independent monotonic
witness or fail-closed per-operation external record BEFORE execution. No
claim of rollback resistance; production recovery remains blocked.
Psycopg connect/statement timeouts do not guarantee bounded total COMMIT time;
a separately supervised adapter process and verified trust configuration
are required. The TLS worker deadline excludes DB and cleanup, and DNS
live path is absent. No live API, owner registration, password, TLS hostname
or Neon endpoint configured. No service creation or changes to build/runtime.
Pin versions are proposed direct dependencies, not audited transitive lock.
Python socket fence is not OS-wide isolation. No independent third-party
auditor; review source and exact CI logs. No Stage 2 closure or Stage 3.
