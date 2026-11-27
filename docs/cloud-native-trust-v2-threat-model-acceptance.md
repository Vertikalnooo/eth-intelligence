# Cloud-Native Trust v2 — threat model and release acceptance
Status PROPOSED / NOT READY. Companion to cloud-native-trust-v2.md. No runtime changes, credentials, remote calls or deployment. All gates OPEN. Stage 2 remains OPEN.

## Threat model
| Threat | Design control | Residual / required proof |
|---|---|---|
| Compromised public API | no secrets/bindings/privileged roles | verify negative access from actual service |
| Forged Gateway request / untrusted internet | independently verified owner assertions, strict RP/origin, dual MACs, no arbitrary URL | malicious UI can mislead owner; phishing/DoS not eliminated |
| Stolen broker SQL passwords / arbitrary broker code | DB reconstructs bytes and verifies dual MACs, spent ID/sequence and legal transition | broker can use an already authorized capability once and withhold output; cannot enforce arbitrary outbound requests on Render Free; live NO-GO |
| One compromised verifier | distinct keys and independent proof checks; no signing oracle | shared Cloudflare account admin can compromise both; independent owner evidence needed |
| DB administrator compromise | separate provider Witness + owner-held pins; no runtime admin | DB admin can read MAC keys/alter DB; no absolute prevention; freeze on mismatch |
| Rollback after commit / lost ACK | durable intent, external heads, no retry, read-only recovery | both journals + external pin rollback defeats detection; unexpired capability against restored DB needs quarantine and TTL drain |
| Duplicate/concurrent requests | atomic unique challenge/authorization/checkpoint and CAS | test at least 20 contenders per layer; only one accepted |
| Secret leakage | per-service isolated scopes, no logs, no shared env, no admin | provider control plane and trusted operator remain privileged |
| Lost iPhone / synced passkey | separate enrollment, explicit recovery, UV, revision + owner-held evidence | no hardware identity guarantee; owner signing/custody must be demonstrated |
| Malformed frames / SQL privilege escalation | strict byte contract, bounded inputs, no dynamic SQL, safe search_path, PUBLIC revoked | real role/catalog negative tests, replay and timing audit |
| Clock/cold start/timeout/quota | short expiry, server clock, bounded calls, freeze | availability intentionally sacrificed; remote commit may complete after timeout |
| Fake execution evidence | independent receipt verifier, ledger audit, owner cache confirmation | actual market provenance and egress boundary unavailable now; no live authorization |
 
## Acceptance matrix (all NOT RUN for v2)
A01 Source: document-only delta; pinned PR39 baseline, unchanged app and default Witness config, no deployment workflow changes. Verify draft, main and deployed baseline unchanged. 
A02 Protocol: JS/Python/SQL canonical golden bytes, positive and negative MAC vectors, field swap, audience/env/candidate/epoch/operation substitution, Unicode/duplicates/noncanonical integer and overlength denied. 
A03 Actual local PG17 pgcrypto: separate keys and owner role; verify both MACs in DB not Python. Wrong/missing one, stolen broker passwords alone, arbitrary SQL, old wrappers, replay, key swap, unauthorized SET ROLE/temp/search_path and key read fail; no mutation. 20 concurrent authorized calls one transition; expired after lock denied; rollback test includes unexpired token residual. 
A04 Real local workerd multiple Workers/DOs: separate bindings and keys; Gateway cannot read key/store or mint auth; one compromised test verifier not enough; two independent verifiers validate actual assertion; missing tables/heads deny, no bootstrap entry in runtime; 20 concurrent claims one win; hard kill after commit and before ACK, persistence and replay denied.
A05 Real synthetic ES256 WebAuthn: pin exact origin/RP/ID, UP/UV, wrong challenge, flags, userHandle, counter rollback, zero count revision, cross-origin, expired enrollment, concurrent registration, replay and restart. No real device registration.
A06 Full loopback PG+workerd+Python integration: all state transitions, per-op owner proof, dual MAC, terminal evidence, cache gate, explicit fixed endpoint, no caller-provided URL, no auto retry. One successful synthetic fetch; zero before consume; no live network. Preserve baseline 532 tests, run full push+PR CI, no skipped security tests, network namespace loopback and locked dependencies.
A07 Fault matrix: cut every ack boundary (Trust, Witness, PG commit, broker, receipt export, cache release), kill/restart all processes, partial commits, stale/missing external heads, clock skew, expired capability while locked, timeout, quota, storage failure, restored single ledger, correlated rollback classification, old epoch credential revoke/TTL drain, forged snapshots. No new operation or cache until explicit review. No claim of distributed atomicity.
A08 Provider feasibility separately approved read-only: verify pgcrypto availability on actual Neon, real direct TLS verify-full SCRAM-PLUS require and negative tests, runtime role membership/ACL, no admin or old wrappers. No secret capture. No test should create resources or increase spend without approval.
A09 Separate disabled deployment approval: exact pinned builds, isolated secret scopes, no public Trust/Witness routes or preview, one public Gateway deny-only, broker deny-only, verified free billing/quotas and no auto deployment from draft. No bootstrap or registration.
A10 Separate real iPhone enrollment acceptance: fixed origin, one-time ticket, UV, credential pin in both services, explicit external intent and signed receipt export/verification independent of providers, restart and lost-device recovery plan. A separate trusted owner-controlled journal signer or validated WebAuthn-based equivalent is REQUIRED. 
A11 Separately approved synthetic cloud recovery: no exchange, test persistent state and ACK loss with nonproduction lineage, no restore/reset of existing deployed Witness or Neon without explicit authorization. Verify authenticated read-only snapshots and key rotation/quarantine design. 
A12 Live release gate: independently enforce outbound one-shot and verify actual receipt provenance even with compromised broker, ensure quotas and transport deadlines, independent review of all preceding gates. If free providers cannot supply required isolation, remain disabled rather than weaken controls. No live connection authorized by any of A01–A11. Stage 2 closure requires explicit user approval and separately verified exchange freshness; local/transport freshness never implies exchange freshness. Stage 3–5 locked.
 
## Non-goals
No trade/orders, keys/wallets, WS, Hyperliquid, automatic grants, bootstrap, migration, deploy, provider writes or paid resources. No claim of protection against simultaneous compromise of trusted administrators, credential theft plus owner evidence theft, or total correlated rollback. No guarantee of uninterrupted availability on Free. 
