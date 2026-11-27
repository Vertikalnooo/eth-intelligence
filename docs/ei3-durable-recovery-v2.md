# Durable Authorization & Commit Recovery v2 — offline acceptance contract
Status: design only, not implemented or accepted. Stage 2 OPEN. No live transport.

## Trust boundaries
Owner Trust and Witness each own a separate durable journal and signing key. Neither shares a database transaction, storage credentials, or signing key with the other or the broker. PostgreSQL remains a separate authorization verifier. A local SQLite fixture can model independent stores but cannot prove cloud independence or anti-rollback of both stores. No production provisioning.

## Spend before release
Each verifier independently validates the exact canonical EI-CAP-3 frame and its local pinned ledger, key epoch, checkpoint, sequence and prior authorization. In a serialized durable transaction it records the authorization ID, frame digest and next checkpoint as spent before releasing its domain-separated HMAC. Unique IDs and checkpoint constraints prohibit conflicting issuance. No re-release after lost acknowledgement; partial issuance or ambiguous persistence quarantines the attempt. Never roll back an external spend because PostgreSQL rolled back. No automatic retry. Both journals must agree before any subsequent transition. Signers must not trust broker assertions of commit.

## Commit recovery
Broker uses a dedicated TLS verify-full/SCRAM channel-bound session, synchronous_commit=on at transaction scope, explicit COMMIT and no release on SQL OK alone. A separately authenticated read-only auditor queries committed event, state, grant and spent checkpoint from a fresh connection. Match exact authorization ID, body digest, checkpoint, owner head, epoch, sequence and operation against both journals. Missing, conflicting or unavailable evidence, lost COMMIT acknowledgement or stale audit freezes the attempt; no automatic retry or external action. Recovery evidence may classify outcome but never resurrect a spent capability. Audit receipts must bind challenge, audience and expiry and be authenticated independently of broker. No actual external action in this phase.

## Restore and rotation
Compare both external journal heads with committed PostgreSQL state before issuing new capabilities. Any divergence or unavailable store fails closed. A restored DB may accept an old valid MAC; broker must never release on that basis. DB-side replay after restore is not solved by external journals alone. Recovery requires explicit operator approval, revoke old logins, drain maximum TTL and in-flight sessions, reconcile committed evidence, rotate both independent keys and pin a new epoch across all stores. No automatic unfreeze, reset or bootstrap. Simultaneous rollback of every trust anchor is outside this model and must not be claimed detectable.

## Acceptance tests (pending)
- Concurrent same-ID and competing checkpoint issuance in each journal; exactly one durable spend.
- Crash before and after journal commit; partial dual issuance; lost signature response; no re-release.
- Actual PG17 CONSUME race, rollback, commit loss via connection fault, async-commit rejection at broker boundary.
- Restore old PG snapshot while independent journals retain newer heads; deny release even if SQL accepts old token.
- Wrong domain/key/epoch, stale or forged audit receipt, inconsistent state/event/grant, role ACL and unavailable audit.
- Rotation with old capabilities and stale sessions; quarantine survives restart; no runtime reset.
- Full offline CI; no network except local fixtures; no cloud modifications or trading.
