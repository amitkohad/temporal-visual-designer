# Server form validation increment

Based on merged main `7838704` (PR #7). Branch: `codex/008-form-validation`.

Implemented the server-side subset of Feature 03 forms. `form_types.py` freezes versioned JSON Schema rules, trusted gateway snapshots and immutable preview contracts. `form_definition.py` checks supported form definitions; `forms.py` supplies authorized preview and partial submission validation; `form_export.py` exports portable schemas. AuthoringValidator now reports form-definition failures during project publication.

Independent agents owned definition checks/tests and service acceptance tests. The coordinator implemented the service, publication integration, exports and privacy regressions. Review identified user regex backtracking and missing dependent-schema required hints; the supported subset now rejects authored regexes and handles dependentSchemas hints.

Run `uv run pytest nexusflow/tests/designer_forms/test_form_definition.py nexusflow/tests/designer_forms/test_forms.py nexusflow/tests/designer_forms/test_form_integration.py` independently. These tests need no Temporal, Entra, network or external storage. Export contracts using `uv run nexusflow-form-schemas`.

Coverage includes required/type/range/format validation, conditional visibility and required rules, read-only/unreadable tampering, secret redaction, partial merge, stale/concurrent updates, grant revocation at commit, invalid JSON and schema rejection. Publication checks distinguish credential-like schema field names from embedded credential values.

This is the backend contract/validation portion of F03-T003 and partial T001/T007/local T008 integration. React builder/renderer and keyboard acceptance, task/start adapters, attachment service, richer layouts and general expressions remain open. F03-T003 remains unchecked until its complete scope is verified. See [ADR 0006](../../docs/decisions/0006-form-validation.md).

Verification: 365 isolated tests passed on both Python 3.12 and 3.13; all 6 real Temporal integration tests passed. Ruff lint/format, strict mypy, locked dependency sync, all four schema export commands and distribution build passed. Independent form-only suites can be run without network/services.
