# Unified production candidate and dependency validation (offline)
Stage 2 OPEN. PR #36 only; no production edits/deployment, no Neon, no live
exchange, no WS or trading. Base 50a3a8607195c44a14fb8526aa3603741b960ae2.

## Design
- Unified controller explicitly constructed only with loopback LocalPg,
  pinned WebAuthn Ceremonies, separate witness and LocalTLS. The old research
  components remain regression fixtures. All admin ceremonies are in-process,
  not HTTP-accessible. Public handler is read-only; default executable
  startup (python production_unified.py) serves disabled rollback API only.
  No flag enables network requests. Existing app.py and Render start command
  are unchanged; current production still uses old market fetch.
- Independent witness prototype: private pre-provisioned directory, pinned
  inode, atomic O_EXCL per (store, original checkpoint), fsync file and
  directory BEFORE first SQL transition, including a version change.
  Failed/unknown transition burns anchor; no retry, deletion or auto-reset.
  The DB checkpoint advances on each transition. A second process with
  same anchor cannot arm even after a DB restore to identical old state,
  provided independent witness is intact. The new checkpoint must be
  independently reconciled, not taken automatically from database.
- Actual Psycopg 3 TLS/SCRAM channel binding with restricted SQL roles,
  actual py_webauthn registration/authentication with synthetic ES256 keys,
  supervised fixed loopback HTTPS worker. Receipt in parent clock captured
  after worker and before SUCCESS commit, publish only after acknowledgement;
  same cache for public status/market/lifecycle; no refetch or persisted
  fresh cache. 30s TTL, diagnostic age 2/5s, flags always false.
- CI: official PyPI wheel download phase (approved); binary wheels only;
  log wheel hashes and resolved packages; install from local wheels and
  execute tests in Linux network namespace with loopback only and Python
  socket fence. No external network during tests, including native libpq.
  Dependencies are direct pinned, not a pre-reviewed transitive hash lock.
  A missing dependency in GitHub CI is failure rather than a skip.

## Remaining gates / limitations
Local witness is NOT an independent remote service. OS/admin with write
access, deletion, correlated rollback or lost witness directory can defeat
it. Never re-pin or recreate after loss; production requires externally
anchored durable witness, operator-controlled provisioning, separate
authorization boundary and recovery process. No free service provisioned.
No real browser enrollment, durable credential counter/enrollment state,
owner session, rate-limited admin UI or live DNS/TLS. No Neon adapter or
actual remote credentials. Runtime role credentials share a trusted
application process in this fixture. No absolute bounded DB COMMIT time;
driver timeouts are not a complete supervisor. Candidate API holds lock
during operation; production needs bounded concurrency and availability.
Lifecycle windows use synthetic monotonic time with actual candidate cache.
No live freshness proof, recommendations or Stage 2 closure.
Safe deployment requires separate approved baseline with upstream disabled
and reviewed Render start/build changes; never rollback to old app.py.
No production startup wiring in this PR. CI workflow change is research
branch only. Review actual CI logs before claiming any dependency tests pass.
