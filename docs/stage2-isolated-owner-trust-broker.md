# Isolated Owner Trust / credential broker — offline only

This candidate is NOT the deployed application. Default app and Cloudflare
configuration are unchanged. There is no live endpoint, enrollment, or
automatic bootstrap/recovery/restart.

Four distinct non-root Linux UIDs: untrusted app (read-only safe status),
owner client (external signatures and WebAuthn), credential verifier
(private credential history), and controller/broker (private SQL and Witness
secrets plus signed journal). Credential verifier has no SQL passwords;
broker cannot read its credential files. Owner private keys are not present
in either service. Each service drops supplementary groups, sets no_new_privs
and non-dumpable, verifies zero effective/permitted/ambient capabilities.
A permanent exclusive fsynced startup receipt denies restart even if no
operation occurred. Use trusted supervisor, private directories, immutable
code, fixed UIDs and environment; no installation on a provider is implied.

IPC is AF_UNIX SOCK_SEQPACKET with mutual SO_PEERCRED checks; strictly bounded
JSON, duplicate keys and unknown fields denied, no arbitrary SQL, paths,
callbacks, secret retrieval, or bootstrap commands. Application receives
only disabled status even after owner-confirmed cache. Owner operations bind
to externally retained journal/credential pins; WebAuthn is verified under
the credential UID for each ARM/ISSUE/CONSUME/ADVANCE, with fixed plan binding
and persisted count/revision before acknowledgement. The broker verifies
signed journal records, computes Witness HMAC internally, never holds owner
signing key. Acceptance of claim/advance uses owner signatures, not a
second WebAuthn ceremony. SUCCESS/UNKNOWN are internal terminal steps of
authorized CONSUME, not exposed IPC commands. Unknown owner mutation result
freezes the broker; no retry. A credential verifier failure freezes further
verification. No auto re-pinning. Recovery remains manual only.

CI uses disposable local PostgreSQL SCRAM/TLS, per-role random passwords,
actual workerd, synthetic ES256 WebAuthn, loopback-only namespace and actual
distinct OS UIDs using setpriv. A trusted runner with sudo provisions fixture
files; it is NOT a production owner security demonstration. No real iPhone,
cloud credential, deployment or exchange connection. Tests include direct
SQL login with old fixture password, private files, process access, wrong
UID, malformed packets, signed chain, credential failure and restart fence.

Threat model: protects against arbitrary code in app UID, not root, trusted
supervisor, owner key theft, or arbitrary code execution in broker UID
(which holds SQL secrets and can directly invoke SQL). Credential verifier
is independent of broker filesystem but the latter is still trusted to
enforce its authorization receipts. No independent cryptographic enforcement
by PostgreSQL, no provider-level UID/network isolation demonstrated. Socket
availability/DoS and combined rollback of private directories and pins are
not solved. No per-role OS subprocess isolation, no end-to-end distributed
deadline, and no production sandbox. The legacy in-process fixtures remain
for regression only. Do not deploy or connect this code to real services.
Stage 2 OPEN; recommendation_eligible=false.
