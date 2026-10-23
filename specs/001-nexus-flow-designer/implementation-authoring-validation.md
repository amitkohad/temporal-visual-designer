# Authoring validation backend increment

Based on main `2358d77`, after merged project-versioning PR #6. Implementation branch: `codex/007-authoring-validation`.

Implemented the backend subset of Feature 03 graph/type/scope validation. Contracts and schema export live in `nexusflow/modules/designer_forms/types.py` and `export.py`; `graph.py` validates structured normal flow and reusable references; `mappings.py` validates activity inputs and variable availability; `validation.py` composes the checks through the existing project publication port.

Three disjoint lanes covered graph implementation/tests, independent publication acceptance, and mapping/composition/integration. Independent review identified failed-producer output availability; direct recovery and recovery-merge regressions now reject those mappings.

Run `uv run pytest nexusflow/tests/designer_forms` independently without Temporal, Entra or network. The acceptance suite uses the actual SQLite project store with fake authorization/catalog ports. It verifies successful immutable publication, rejected invalid drafts, unchanged release state after catalog failures, branch scope and recursion. Export the binding contract with `uv run nexusflow-binding-schema`. The proof runtime rejects bindings until runtime evaluation is implemented.

Scope: partial F03-T001/T002/T007 and local backend integration only. F03-T003 forms, T004/T005 React UI, T006 templates, full browser acceptance and T008 sibling integration remain open. No component/keyboard design handoff or production runtime support is claimed. See [ADR 0005](../../docs/decisions/0005-authoring-validation.md) for the supported mapping subset and remaining semantics.

Verification: 288 isolated tests passed on Python 3.12 and 3.13, including 44 designer checks and 2 new runtime rejection regressions. All 6 real Temporal integration tests passed. Ruff lint/format, strict mypy, locked dependency sync, all schema exports and distribution build passed. No Azure or Microsoft credentials were used.
