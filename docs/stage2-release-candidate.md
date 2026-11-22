# ETH Intelligence Stage 2 Release Candidate

**Disposition:** Offline research candidate for review. Stage 2 stays open.
Stage 3 requires a separate decision.

## Source and scope

The candidate starts from Draft PR #29 at
`327dabc52347ea122ce8ff101c255e61319ad6ce`. The comparison base is
`main` at `b86fe378e7c0fbf19044a24a17c552bbe63b319a`.

| PR | Verified source HEAD | Integration in the candidate |
| --- | --- | --- |
| #20 | `41f6a1bd333b8cd1975397c0670c5c71010d2f8d` | Provenance files and tests imported in #22. Later revisions supersede the replay and gate changes. |
| #21 | `0dbbf93552e58acd927de096ccc8e4aed776808f` | Ancestor. Local transport freshness and REST/WS comparison. |
| #22 | `bfd51ecd72780b897fa608cca5409ffc6309b29c` | Ancestor. Receipt-only adapter and integration harness. |
| #23 | `62c5e60b8d3d212d26d4fad8997a2fbc5ff51205` | Ancestor. Revocation, input validation and pair and replay diagnostics. |
| #24 | `458f9c2d72963b481b359986ab7853c553521b6d` | Ancestor. Serialized lifecycle, fork isolation, shared adapter and SQLite binding. |
| #25 | `3f720f71f82d804f2fab8962d2fd15222fa23345` | Ancestor. Deny-only trust claims and evidence requirements. |
| #26 | `6145b571e16074dd4f3eea71e51223bbd82a33d0` | Ancestor. Metadata validation precedence and operational contract. |
| #27 | `231deda1f7864afb695d1405ee1645a13dc30edf` | Ancestor. Unified offline report and acceptance tests. |
| #28 | `fa4d282525d5f81680ece0fb54d0b5cb6b9bebe5` | Ancestor. Structural binding and process-local snapshot serialization. |
| #29 | `327dabc52347ea122ce8ff101c255e61319ad6ce` | Exact baseline. Removes all unified replay writes and I/O. |

GitHub ancestry confirms #21–#28. PR #20 has divergent commit history.
All 15 paths changed in #20 are present in #29. Eleven blobs match exactly.
The four revised paths are `provenance/decision_gate.py`,
`provenance/replay.py` and the v1.1/v1.2 hardening tests. The candidate
keeps the cumulative versions, including later safety changes.

## Current diagnostic contract

| Output | Interpretation |
| --- | --- |
| `transport_state` | Operational Freshness of the primary REST observation from caller-supplied local monotonic receipt age. With the default policy, FRESH is at most 2 s and DEGRADED is over 2 s through 5 s. |
| `consistency` | Both receipts evaluated against the same local monotonic instant. WS requires a matching connected session epoch. Pair results retain side-specific reasons for invalid input, disconnect and stale receipts. |
| `exchange_state` | Always `UNVERIFIED` in the unified report. Invalid receipt or claim data appears in its own diagnostic state and reason codes. |
| `trust_claim_state` | `INVALID` or `UNVERIFIED`. A claim must refer to the corresponding REST or WS observation in the report. Malformed metadata keeps its validation reasons. |
| `binding_structurally_matches` | Structural consistency with the REST receipt and any supplied event/time assertions. A match supplies no issuer authentication. |
| `replay_result` | Always `UNAVAILABLE` in the unified report. Requested replay is labeled `synthetic_replay_disabled`. The standalone synthetic SQLite path uses temporary stores and returns diagnostic outcomes only. |
| `accepted`, `recommendation_eligible`, `stage2_complete` | Always `False` for reports returned by the unified entry point. |

A positive local diagnostic cannot establish when the exchange updated
the underlying mark. Synthetic helper APIs preserve compatibility with
legacy research fixtures. Their results have no authority over admission.

The clock-domain lock serializes this in-memory report with revoke and
rotate. A forked child and a fresh interpreter start with no inherited
authorizations. `snapshot_consistent` refers to the local domain state
during evaluation and is not a storage transaction or proof of the
origin of a supplied observation.

## Audit findings addressed in this candidate

| Finding | Correction and regression evidence |
| --- | --- |
| A valid WS receipt in the primary REST slot could yield a positive adapter flag. | Require a REST receipt before mapping. A swapped pair now gives INVALID transport, INVALID_INPUT consistency and a rejected adapter. |
| A claim about an unrelated receipt could be reported alongside a consistent pair. | Bind the claim to the exact diagnostic observation by validated value and report an explicit mismatch reason. |
| Caller-provided fields could invoke property or equality methods under the domain lock. | Check concrete observation and field types before property access or equality. Tests ensure malformed objects are rejected and do not consume a replay key. |
| A binding could conflict with event or timestamp assertions carried by the REST observation. | Compare both observation and claim assertions with the structural binding. |
| The unified exchange verdict could reflect lower-level validation state. | Emit `UNVERIFIED` for every unified report and retain separate validation reasons. |
| Earlier documents describe superseded replay behavior. | Use this document for the current contract. Keep prior reports as historical evidence. |

## Acceptance gates

| Gate | Evidence required | Disposition |
| --- | --- | --- |
| Research candidate | Full Python 3.12 unittest discovery, exact candidate SHA green in GitHub Actions and reviewed diff. | Local baseline 312/312. Candidate 318/318 locally. Exact-SHA CI is recorded in the PR checks. |
| Exchange evidence | Independently authenticated exchange event with exact ETH mark/time binding and trusted UTC before any verified exchange status could be considered. | `UNVERIFIED`. Current candidate has no trusted clock or verifier. |
| Live operational lifecycle | Separate authorized validation of receipt latency, reconnects, source availability, and restart/capacity behavior on an isolated deployment. | Open. Thresholds and process-local registry are research settings. |
| Storage boundary | Agreed filesystem identity, retention, tamper, restart and multi-host requirements before using persistent replay outside synthetic diagnostics. | Open for deployment. Temporary local SQLite tests cover transaction and contention semantics. |
| Deployment controls | Confirm CI-based deployment policy, deployed-SHA evidence, monitoring and rollback before production wiring. | Open. Historical Render observations are in the [prior review](stage2-acceptance-review-20261122.md). |
| Stage 2 decision | Review this candidate and explicitly decide whether to accept operational-only scope or require verified exchange provenance. | Pending user decision. |

Run `python -m unittest discover -s tests -v` from the repository root.
The suite uses synthetic fixtures, temporary SQLite databases and the
existing loopback HTTP fixture. The `Tests` workflow is triggered by push
and pull request. The separate historical live inspection workflows are
outside the offline acceptance run.

## Final Stage 2 hardening pass — independent verification gate

The research branch now requires an **offline network fence in CI**:
Python starts with `scripts/offline_guard/sitecustomize.py` and the
workflow verifies outbound TCP, connect_ex and UDP are denied before running
tests. Local loopback HTTP and Unix sockets remain permitted. All subprocess
Python interpreters inherit the same PYTHONPATH. The dedicated guard proof
deliberately never initiates an external connection unless the fence is active.
This is a defense-in-depth CI control, **not** a network sandbox for every
possible native binary or an assertion about any production deployment.

A cloned `LocalClockDomain` token in the same process is now rejected:
the registry requires exact identity of the issued in-memory object, not
merely matching process ID and token. This makes the diagnostic lifecycle
unambiguous but **does not authenticate an exchange clock**.

Normal `Stage2OfflineReport` construction and dataclass replacement cannot
set `accepted`, `recommendation_eligible`, or `stage2_complete` to True.
These are still ordinary Python objects and must never be treated as
cryptographic access-control capabilities. The separate production
recommendation gate remains deny-only.

Additional negative offline tests exercise copied domains, disabled cloning
through adapter and report, immutable denial flags, stale/disconnected/
mismatched/old-epoch outcomes, and rotation. No changes to `app.py`,
`render.yaml`, production endpoints or trading behaviour were made.

### Outstanding external acceptance items

1. **Production release policy:** historical Render configuration is
   `autoDeployTrigger=commit`, not proven `checks_pass`. No Render
   reconfiguration or deployment is authorized here.
2. **Authenticated mark freshness:** no independently verifiable update time
   exists in this implementation. `Verified Exchange Freshness=UNVERIFIED`,
   regardless of a local FRESH observation.
3. **Live operations:** production deployed-SHA, mark throughput and WS
   reconnect behaviour have **not** been re-tested here. Historic metadata
   and simulated tests are insufficient for operational acceptance.
4. **Multi-host persistent replay:** SQLite tests establish local synthetic
   collision safety, not a distributed security boundary.
5. **Final approval:** the research candidate must receive an independent
   review and explicit user approval. Stage 2 stays OPEN; Stage 3 prohibited.

GitHub Actions must pass on the **final exact commit SHA** (check both push
and PR merge-ref test logs), not an earlier run.
