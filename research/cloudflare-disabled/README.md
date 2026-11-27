# Cloudflare DISABLED deployment (prepared; not authorized to execute)

No deployment or Cloudflare API access during preparation. Source PR39 exact SHA
a4cba20e3308878989ae88c1a376837d99bf5e81. Separate branch based on Safe Baseline main.
No merge, no Render/Neon/exchange access. Worker name eth-intelligence-witness;
SQLite migration v1; DISABLED; workers.dev and previews false; no runtime secrets,
custom domains, routes, triggers, bootstrap or automatic Git build connection.

## Why push gate
GitHub workflow_dispatch requires the workflow file on the default branch.
Main must not change. A future single non-force commit adding only the exact
APPROVED-ONCE.txt marker to this branch is the trigger. Marker is absent now.
Only owner actor, first run attempt, exact branch, one commit adding only the
marker, exact source SHA and blob checks. Do not create the marker until
separately authorized. Do not rerun a failed/ambiguous deployment.
The environment must be configured BEFORE adding marker:
- GitHub Settings > Environments > New environment: cloudflare-witness-disabled.
- Required reviewer: owner; allow self-review only if sole reviewer; restrict
  deployment branches to research/cloudflare-disabled-deployment only.
- No bypass if available. Verify actual protection in UI; stop if unavailable.
- Environment secrets CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN, not repo secrets.
- Environment variable WORKERS_FREE_CONFIRMED=YES only after owner confirms
  Cloudflare Workers Free and no paid upgrade. This is owner attestation, not
  programmatic billing verification. Check GitHub Actions free usage too.
- Token limited to target Cloudflare account, Workers Scripts Edit/Write,
  read metadata as required; short TTL; no global key or billing/DNS privileges.
  If additional permissions requested, stop and review. New Worker creation
  may require account-level Workers permission; it is not cryptographically
  restricted to only one Worker. Do not expose token in chat/screenshots.
- Do not create GitHub environment/secrets/token in preparation stage.
After separately approved marker push, approve environment only on exact run,
then verify dashboard and revoke token immediately; remove environment secret.
No automatic rollback/delete. Existing target Worker/namespace aborts; race
with another account admin not excluded. Keep no other deployment concurrent.
Preflight and postflight use API GET only; Wrangler deploy is sole write.
CLI logs suppressed to avoid public disclosure. Postflight checks mode,
binding/class, SQLite namespace, public and preview URLs, custom domains.
No public endpoint invocation; actual object ID, SQLite contents/persistence,
and authenticated runtime receipt not verified by metadata. Namespace ID
record manually from dashboard, separate from object ID and logical store ID.
No bootstrap, no checkpoint yet. Do not claim real data durability.
If API response differs, credentials fail, or deployment result uncertain,
stop, inspect dashboard, revoke token; no reruns or automatic cleanup.
Public repo: do not save account IDs, namespace IDs or tokens to Actions logs,
artifacts, marker or source. Use owner-private Files > On My iPhone and separate
locked note for SHA, namespace ID, deployment/version IDs and timestamp; no keys
or tokens in note. No real owner journal head exists until approved bootstrap.
