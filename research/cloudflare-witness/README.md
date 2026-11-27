# Cloudflare witness research candidate
No deployment or Cloudflare namespace provisioned. Default Wrangler config disables
worker routes, previews and claims. No deployment workflow, npm dependency, secret,
or live service access. The OFFLINE_FIXTURE setting exists only to exercise local
tests. Never activate it on a remote Worker. Production needs a distinct mode and
reviewed transport and authorization. Workers Free supports new_sqlite_classes.

Contract: POST /v1/claim over HTTPS, canonical compact JSON ordered store, checkpoint,
candidate, epoch, grant, issued_at (Unix seconds). HMAC-SHA256 of UTF-8
EI-WITNESS-OFFLINE-V1 + newline + POST + newline + /v1/claim + newline + body.
Hex 32-byte synthetic key, no query string, body <=1024 bytes, max age 30 seconds.
Only configured store/checkpoint/candidate, fixed object name, no automatic next
checkpoint. 201 spent only after synchronous SQLite transaction and storage.sync;
409 already_spent, 403 deny, 503 disabled/unconfirmed. No retry after ambiguity.
Primary key excludes candidate to prevent reuse across code upgrades. No reset,
delete, list, registration or grant issuance endpoint. Health is diagnostic only.
No browser access or secrets in source. HMAC proves shared-key possession, not owner
Passkey; owner separation, per-operation signed approval and a trusted checkpoint
advance/reconciliation protocol remain production blockers. Secrets must be placed
in Cloudflare secret storage, never source or chat; no real secrets provisioned.

Test adapter uses actual Node SQLite on disk, same SQL and transaction callback,
Web Crypto HMAC and Request/Response. It is NOT workerd or Cloudflare persistence,
does not model platform output gates, network body stalls, platform quotas, or
PITR. Runtime integration and namespace identity/restore tests remain required.
If both Neon and witness roll back, or the namespace is replaced/lost or operator
changes config, equality/uniqueness is insufficient: external owner-pinned
monotonic receipt and freeze/reconciliation needed. Table creation is lazy and
cannot distinguish namespace loss; do not allow production initialization this way.
Do not connect Render or Neon. Stage 2 OPEN; recommendations false.

Local tests: Node 22+ `node --test research/cloudflare-witness/test/*.test.mjs`.
No dependency installation. CI tests are isolated in loopback-only Linux namespace.
