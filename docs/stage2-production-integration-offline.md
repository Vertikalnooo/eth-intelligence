# Production Integration Offline Implementation — Stage 2

Research candidate stacked on PR #35 head 9afb2ca63a4e502f9beca4545e9d732d6dd97260.
Stage 2 OPEN; Stage 3 NOT STARTED. No deployment, external database, real owner
credentials, exchange requests, WS or trading authorized. Existing app.py and
Render startup are unchanged; the old production /market/eth still fetches
upstream. Do not confuse this candidate handler with the deployed API.

## Implemented

- PostgreSQL: actual schema, transactional SECURITY DEFINER routine with fixed
  search path, pinned store identity, fresh process epoch, candidate binding,
  one-use grant and append-only transition events; global serialized active
  attempt and 35s cooldown after success, frozen after unknown. Runtime role
  cannot read/write/delete tables directly. No automatic unfreeze or migration.
  Local psql adapter uses a Unix-domain socket only, no remote DSN or secrets,
  fixed database/user, strict identifiers and bounded subprocess timeout.
- Owner: pinned pre-provisioned ES256 public key, actual OpenSSL signature
  verification, WebAuthn client challenge, exact localhost RP/origin, UP/UV,
  credential binding, purpose/grant binding, one-use 60s challenge; separate
  assertions for issue, consume and revoke. No browser registration or UI.
- TLS: isolated subprocess with fixed 127.0.0.1, verified test CA and IP SAN,
  TLS >=1.2; inherited process death and deadline safeguards, maximum 8s
  worker budget, bounded response parser, no redirect/retry. No live hostname.
- Candidate routes: local /health and /market/status; cache-only /market/eth;
  POST challenge, confirm, revoke, execute. Challenge allocation is bounded
  but not itself authenticated (no assertion yet). No owner audit HTTP route
  or public listener. No startup hook. All denial flags stay false.
- Cache: only publish after successful committed terminal event; use parent
  monotonic receipt and same cache lock for status, market and lifecycle;
  cache TTL 30s; no restoration from historical observations. Tests use
  actual local cache and synthetic monotonic time for lifecycle windows.
  A failed/unknown attempt clears cache, freezes local controller and records
  UNKNOWN if possible; unresolved active reservation remains blocked.

## Remaining production acceptance blockers

1. Neon connectivity, TLS libpq driver and connection configuration are not
   implemented or tested remotely. Local psql is intentionally restricted;
   replace with reviewed bounded driver, verify TLS hostname, roles, schema,
   durability, quotas, restore policy and externally pinned store identity.
   PostgreSQL administrator, compromised runtime credentials and database
   rollback remain trusted-boundary risks. A role with EXECUTE can invoke
   the routine directly; passkey verification is enforced by the trusted
   application, not by the DB. No claim of independently authenticated ledger.
   If SUCCESS committed but acknowledgement was lost, local freeze cannot
   prove the DB froze; require external startup/recovery fence before live.
2. WebAuthn implementation is an offline assertion subset using a synthetic
   signing key, not full browser registration or audited WebAuthn library.
   No real passkey enrollment, user session, CSRF/rate limit/recovery or HTTPS
   admin browser UI. Use reviewed WebAuthn library, real RP domain, owner-only
   enrollment and verify actual iPhone flow before any enablement. Challenge
   endpoint must be abuse protected. No private trading key needed.
3. TLS tests cover local certificate/IP verification, untrusted certificate,
   redirect, 429, stalled response. No real DNS/TLS exchange validation.
   Parent deadline starts after reservation, cleanup and OS scheduling caveats
   remain; real network worker requires a separate review and explicit scope.
   The candidate conservatively freezes on any failed worker, including 429,
   rather than automatically recovering. Real Retry-After and existing rate
   limit policy need reviewed migration. No implicit retries.
4. Candidate API is NOT mounted in app.py; replace public fetch route only in
   separate reviewed integration, preserve existing status contract or version
   it explicitly, add audit/history endpoints and durable observations if
   required. No deployed endpoint behavior changed. A 35s interval prevents
   overlapping successful cache windows, but process restart invalidates cache.
   Process epoch prevents old grant reuse, not privileged reconfiguration.
5. CI fixture creates disposable local PostgreSQL and synthetic keys only.
   Python socket guard is not an OS-wide sandbox. psql uses Unix socket with
   no external host, local PostgreSQL listens on no TCP addresses. Fixture
   requires runner preinstalled PostgreSQL and OpenSSL (no package download).
   The Render build image has not been verified to have them.
6. Stage 2 OPEN. Separate review for production design, explicit approval of
   real Neon provisioning, credentials, implementation changes, merge/deploy
   and a later exactly-one authorized live attempt. Never automatically recover
   or issue a new grant after an unknown outcome.

## Test and review gate

Exact head push and PR merge-ref offline CI, actual local DB and TLS counts,
role permissions, cryptographic signatures, replay/expiry/epoch checks,
concurrent consume, freeze, public route denial, cache lifecycle and original
regression suite. No live CI workflow dispatch.
