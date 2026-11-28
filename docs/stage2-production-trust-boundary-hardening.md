# Production Trust Boundary Hardening — research only

All modifications are offline, in Draft PR39. No deployment or bootstrap of
real infrastructure. app.py and default Wrangler entry/config remain disabled.
Existing deployed SHA is unchanged. No exchange access.

## Local guarantees and residual limits

CrossController requires DurableCeremonies and an exact Journal. Every ARM,
ISSUE, CONSUME, ADVANCE verifies the independently signed latest record and
external head before WebAuthn or side effects. Direct calls without a valid
journal fail. OwnerSession is not an alternative authority. Cache remains
unavailable until the signed ADVANCE_RECEIPT binds plan, actual result,
monotonic receipt timestamp and Witness head, and the caller supplies the
matching external journal head. Expiry still uses original receipt time.

CredentialStore is append-only per private local directory, uses exclusive
create, flock, fsync, identity/counter invariants and independently supplied
latest head. No implicit enrollment. install_verified requires an explicitly
verified registration result and independently pinned key fingerprint.
Zero counters (synced passkeys) do not provide clone detection; challenge
freshness and durable revision still apply. Never automatically adopt disk
head, recreate store, or recover after unknown write. iPhone enrollment,
secure owner-controlled custody of both latest heads, and recovery of a lost
device remain separate acceptance gates.

Network SQL (including COMMIT), Witness transition and terminal audit calls
through CrossController are enclosed in a Linux child process with an
absolute monotonic budget (8s), parent-death signal, bounded result and kill/
bounded reap. No automatic retry. Remote server may still commit after local
timeout; freeze and reconcile manually. Fork from threaded Python is not a
production isolation primitive; a future dedicated executable/UID and
restricted network/credential broker are required. Each call has its own
budget; not a single end-to-end transaction deadline. Historical offline
candidates remain fixtures, not approved entrypoints.

Recovery uses actual TLS/SCRAM local PostgreSQL audit role and a separate
read-only workerd entry with pinned TLS certificate, independent HMAC audit
key, nonce and authenticated response. It compares signed owner journal,
exact terminal PG state/grant/events, Witness identity, sequence, checkpoint,
last two event bodies/digests and spent checkpoint. No caller snapshots.
The audit entry cannot transition or initialize. Recheck PG/journal; this is
not an atomic distributed snapshot or full historical audit. Only completed
cycle can yield MANUAL_REVIEW_ONLY, never execution authorization.

## Production blockers intentionally not waived

Python method checks are not a security boundary against arbitrary code in
the same process: caller with raw adapters/credentials, OS user or cloud
administrator can bypass them. No production process separation, durable
owner-held external pin exchange, authenticated iPhone enrollment UI,
Neon remote TLS/roles verification, protected Cloudflare gateway, or remote
recovery acceptance has been performed. Need isolated owner service with
durable credential and approval state, separately held keys, dedicated
least-privilege broker, one-use operation capabilities and no exposed raw
adapters; production API must never have bootstrap/admin credentials.
Correlated rollback of both history and external pin is outside local
guarantee. Do not deploy audit-offline config. No remote endpoints added.
Stage 2 remains OPEN, recommendation_eligible=false.
