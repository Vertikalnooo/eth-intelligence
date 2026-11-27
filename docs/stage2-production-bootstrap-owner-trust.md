# Production Bootstrap & Owner Trust — offline acceptance

PR39 research only. Stage 2 OPEN. Existing app.py remains SAFE_BASELINE.
Default Wrangler config still uses hardened.mjs DISABLED. The separate
wrangler.bootstrap-offline.jsonc uses bootstrap.mjs, never deploy it.
Explicit bootstrap request binds object ID, store, identity, candidate,
checkpoint, owner public key, 32-byte token and 30s expiry. HMAC plus separate
P-256 owner signature, token hash pinned out of band; atomic CREATE and seal,
sync before receipt. Existing tables deny. No token stored in plaintext in
the journal. Before request the owner-side journal appends signed intent,
O_EXCL and fsync, only on empty pinned journal. Any uncertainty blocks retry.
Switch to normal runtime after genesis; bootstrap endpoint not available.
The external journal verifies each signed record and expected separately
retained digest; no automatic re-pin. OwnerSession requires signed CLAIM and
ADVANCE intent/receipt and the claim pin before ISSUE/CONSUME. It is research
orchestration, not the only callable entrypoint, not an independent process
or hardware boundary. Local filesystem must remain outside both restore
boundaries; do not recreate after loss. A separate owner-held digest is
mandatory; tests simulate it, actual iPhone storage/export not implemented.

## Recovery runbook (no automatic repair)
1. Freeze all operations, preserve old epoch, disable bootstrap and all
   credentials capable of writes; do not retry after timeout/unknown.
2. Obtain read-only PostgreSQL state, exact grant/event history, authenticated
   Witness identity/sequence/head/mode, and owner journal plus independently
   pinned latest digest. Do not adopt a database value as a trusted anchor.
3. Run recovery classifier: pending intent/receipt, missing journal, pin
   mismatch, PG checkpoint/mode/active mismatch, Witness mismatch => FROZEN.
   Matching final snapshot => MANUAL_REVIEW_ONLY, never authorization.
4. If any mismatch, investigate both providers' restore/deployment audit,
   retain evidence of every possible upstream attempt. No automated reset,
   rebootstrap, advance, or replacement of identity. Privileged recovery
   needs separately approved design and explicit owner decision.
5. Even matching snapshots require independent historical reconciliation,
   cooldown and a newly scoped authorization. No second cycle enabled here.

## Safari preparation
Fixed proposed RP eth-intelligence-api.onrender.com and HTTPS origin.
Separate profile in library-backed Ceremonies, tested with synthetic signed
credential for that RP; cross-provider controller explicitly rejects it.
JS converts WebAuthn binary fields and serializes assertion; ceremony
requires secure exact origin and explicit user gesture. No HTTP admin route,
session, CSRF/rate limit, actual iPhone enrollment or hardware key custody.
WebAuthn assertion is not arbitrary ECDSA signing; independent Witness
owner key remains distinct. Do not claim actual Face ID or Safari validation.

## Dependency integrity
Ten exact wheels from matching PR39 CI SHA256 logs, CPython 3.12 Linux x86_64.
Download and local install enforce --require-hashes and binary wheels only.
npm lock retained. Setup downloads official registries before loopback-only
test namespace. No vulnerability audit for Python implied by hash integrity.

## Remaining gates
Cloudflare bootstrap replay after entire namespace loss with still-valid
token and owner packet is possible if bypassing owner-side gate; explicit
bootstrap-only deployment removal, external anchor, separate owner process
and secure provisioning needed. Owner journal not immutable against its
administrator or correlated rollback with external pin. Recovery inputs
are supplied snapshots, not independently authenticated live queries.
No cross-provider atomic transaction, no absolute network deadline.
No actual Neon/Cloudflare/Render changes, live requests or trading.
