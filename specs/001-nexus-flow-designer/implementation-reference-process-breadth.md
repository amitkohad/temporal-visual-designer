# Reference-process system breadth (NF-T016b / rollback + old-run)

Branch: `feature/reference-process-breadth-29b7`.

## Inventory vs NF-T016a (PR #86)

| Acceptance / SC item | NF-T016a | This slice |
|---|---|---|
| SC-01 Dev lifecycle both starters | ✅ lifecycle | unchanged |
| Purchase Dev→UAT promote + optional Prod evidence | ✅ promote | unchanged |
| Rollback during in-flight reference run | remaining | ✅ rollback test |
| Old-run completion after rollback / future start pin | remaining | ✅ |
| Full NF-001–012 product surface / browser / Entra | remaining | not claimed |
| SC-02 isolated suite completeness | remaining | not claimed |
| SC-03 exhaustive cross-tenant matrix | remaining | not claimed |
| SC-04 every concurrent activation/restart edge | remaining | critical-path only |
| Onboarding package promote parity | remaining | deferred |
| Production providers | remaining | not claimed |

## Implemented boundary

Critical-path system fixture `nexusflow/tests/system/test_reference_process_rollback.py` for **employee-onboarding** and **purchase-requisition** against local-demo / signed local providers + owned Temporal:

1. Publish + activate release A → start run → wait for first pending approval.
2. Save a non-breaking draft (notes `maxLength` tweak) → publish release B → activate B; assert the in-flight run stays pinned to A.
3. Rollback to A; assert activation receipt `action=rollback` and current pointer restored.
4. Complete all remaining approvals; assert the old run finishes still pinned to A.
5. Start a new execution after rollback; assert the accepted start resolves release A (no second full approval cycle required for this claim).

Helpers extended in `nexusflow/tests/system/helpers.py` (`bump_draft_revision`, `publish_release`, `activate_release`, `finish_remaining_approvals`, transient-503 retries on task/status polls).

## Verification

```sh
uv sync --locked
uv run pytest -q -m integration nexusflow/tests/system/
```

Result for this slice: **5 passed** (3 prior SC-01 + 2 rollback parametrized cases).

## Explicitly not claimed

- Full NF-001–012 surface, browser UI, Entra, production providers
- SC-02 / SC-03 completeness; exhaustive SC-04 matrix
- Onboarding Dev→UAT package promote parity (purchase already covered in NF-T016a)
- UAT/Prod managed-approval execution

Parent **NF-T016** remains open. Nested **NF-T016b** is checked for this rollback/old-run critical-path slice only.
