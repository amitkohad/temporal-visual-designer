# ADR 0034: Materialize staged projects into target releases

Status: accepted for the local backend increment.

A staged package is an inspected snapshot, not an executable target release. Add a separate, freshly authorized `package.import` operation to materialize it. Activation remains a distinct permission and operation.

## Destination transaction

The request supplies a stable operation ID, expected target project revision and destination configuration revision. Zero requires an absent project. Materialization creates a target revision and immutable release, advances the draft head, retains the release, and records its receipt in one SQLite project-store transaction. Existing project names are preserved. Source release/revision remain receipt metadata; target release identity and revision belong to the destination. Project revision compare-and-swap is separate from activation generation compare-and-swap.

The operation identity is scoped by organization/environment. Exact retries return the first committed receipt, including its original actor and timestamp, before reopening the staged archive or checking mutable providers. Changed stage/request identities conflict. Permanent release retention keeps receipt lookup usable after restart. Existing stores gain an additive table.

## Trust and readiness

The package organization must match the authorized destination. Every referenced source artifact must already exist in the destination's approved catalog with matching validated manifest/source identity and a non-withdrawn state. Import does not automatically publish or execute uploaded Python. Raw internal catalog ports operate under the import authorization; production support does not need the unrelated export or draft-authoring permission.

Before committing a release, validate project integrity, supported proof-runtime structure, destination configuration and worker capabilities. Retain artifacts before the project transaction; failed or racing requests may leave conservative holds. No automatic cleanup is introduced. Activation repeats its own readiness checks; the receipt's configuration revision records the materialization check, not an active binding.

Checksums establish identity, not signatures or provenance. Destination publication is the approval boundary for custom artifacts. Production signing, isolated custom execution, cloud providers, retention management and Admin screens remain separate work. Synthetic identity/provider tests establish no live Entra or production deployment acceptance.
