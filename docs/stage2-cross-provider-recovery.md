# Cross-provider offline acceptance (Stage 2 OPEN)

Exact source snapshots: main 11d0a6040abbd1bc1ab5fd76bb205e8e8032b23b,
PR35 9afb2ca63a4e502f9beca4545e9d732d6dd97260,
PR36 a4dda27ba4645b535c6735830e63f58d666197b4 (stacked on PR35),
PR38 7f2a6f6adbab631ac9aec9fd711073c81cb67801.
Single-parent research commit copies source additions by blob SHA, preserves
main app.py and baseline tests, reconciles CI and adds ADVANCE ceremony.
No original PR refs changed. No merge, deployment, live services or credentials.

CrossController is in-process only, fixed loopback transport. Explicit local
fixture provisioning of Witness and PostgreSQL; missing state fails closed.
Owner supplies separately signed canonical Witness packets and WebAuthn
assertions (synthetic authenticator, real library). One immutable plan binds
store, identity, object id, previous head, sequence, SHA, epoch/grant and
four SQL checkpoints. Local external intent marker (existing Witness) burns
before remote witness CLAIM; then actual SQL ARM, ISSUE, CONSUME, one
supervised TLS request, SUCCESS. Audit role reads consistent read-only DB
snapshot, exact terminal state/grant and ordered events for this grant.
ADVANCE requires separate WebAuthn plus separately signed packet whose next
checkpoint equals terminal SQL checkpoint and evidence equals computed hash.
Audit again after acknowledged ADVANCE; cache visible only after successful
advance, never restored on restart. 900s SQL cooldown remains intact.
No retry or recovery from ambiguity; terminal failure freezes locally.
An acknowledged advance returns a receipt for manual external pinning only.

Limits: owner private key and separate audit role exercised in same trusted
test process, not separate production principals. Owner-side intent marker
uses trusted local FS, no iPhone durability; no protection from deletion,
re-pinning, privileged DB/DO modifications or simultaneous rollback including
external marker. Actual owner receipt storage, independently provisioned DO
identity, trusted external audit attestation and recovery runbook still open.
The DO checks signed hash, not Neon directly. Audit before/after advance is
not distributed atomic commit; rollback between checks or after them remains
a separate recovery problem. No automatic rearm, second-cycle proof or
production bootstrap. PostgreSQL timeout and Witness socket timeout are not
absolute deadlines. Fixture pins certificate from local setup handshake,
not real remote certificate validation. No proof of cloud durability, real
iPhone passkey or exchange freshness. V2 offline mode must not be deployed
as production. Existing SAFE_BASELINE app remains unchanged and disabled.
Tests run inside loopback-only Linux namespace; official PyPI and npm
downloads before isolation, exact npm lock preserved; Python direct pins
retained (full hash-lock not enforced in this PR). No live requests or orders.
