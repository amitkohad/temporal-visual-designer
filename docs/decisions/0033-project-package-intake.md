# ADR 0033: Durable target-environment package staging

Status: accepted for the local import-staging increment.

Persist inspected project packages in a target organization's environment before any executable release is created. A staged import has state `staged`, a caller-selected operation ID, a scope-local revision, source project/release/revision identity, logical package digest, verified uploader and timestamp. Staging is not publication, executable trust approval, configuration readiness or activation.

Every stage/list/read/manifest/download operation requires fresh scoped `package.import`. Decode the bounded package and require its organization declaration to match the authorized target scope. Reuse the established format, project baseline and source-bundle checks without extracting or executing code. A package's self-declared organization and checksums are not signatures or provenance verification. The target release/persistence/trust gate must not treat a staged record as executable permission.

Use a dedicated SQLite repository with an immutable scope/operation primary key and scope-local monotonically increasing revisions. Persist the original archive, its physical size/SHA-256, manifest and trusted receipt in one immediate transaction. The operation ID identifies the logical package digest: a repacked ZIP with the same indexed package identity returns the original uploader, receipt and first archive bytes. A different digest under the same operation ID conflicts. Exact retries are resolved before quota checks, including after an ambiguous commit response.

Default storage quota is 100 staged records per organization/environment, configurable by the host. Scope-local sequence allocation and quota enforcement serialize across connections. There is no replace/delete endpoint in this increment. History uses bounded descending revisions and exclusive cursors; metadata listing selects neither archives nor manifests. Manifest and download reads verify stored identity/integrity and fail safely on corruption. The existing codec and HTTP upload budgets still apply.

The application factory accepts an explicitly configured optional import repository. An unavailable provider returns 503 only after permission checks; there is no fallback fake store. The local demo opens and closes a persistent `package-imports.db` with its other stores. The new file is additive and is not a mandatory initialization marker, so existing local installations need no reinitialization.

No staging operation writes project drafts, catalog artifacts, releases, active pointers or execution state. Existing published packages can be staged into Dev/UAT by authorized business authors, and Prod by production support under existing policy. Production storage providers, retention/deletion, executable trust approval, target configuration, release materialization and Admin screens remain separate work.
