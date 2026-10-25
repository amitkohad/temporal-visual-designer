# Stage a project package

After [exporting a published project package](project-packages.md), an authorized user can upload it to a target environment for durable staging. Staging validates and retains the package; it does not create an executable release, import activity code into the published catalog or activate workflows.

Use the target organization/environment API base and signed headers:

```text
PUT /package-imports/{operation_id}
Content-Type: application/zip
```

Send raw archive bytes and retain a stable operation ID such as `uat-import-001`. All staging routes require `package.import`. Business authors retain Dev/UAT access; Prod uses the existing production-support policy. The package organization declaration must match the authorized target organization.

The 201 response contains `operation_id`, scope-local `revision`, source `project_id`, `release_id`, `source_revision`, `package_digest`, uploader `actor_id`, `created_at` and state `staged`. Keep this receipt. Retrying the same ID and logical package returns the original receipt and uploader, even if the outer ZIP was repacked. A different logical digest with the same ID returns 409. After an unavailable response, retry the same operation rather than selecting a new ID.

The default quota is 100 staged packages per organization/environment. Exact retries still work at quota; additional operations return 409. Invalid packages leave no staged record. Codec limits are unchanged, including the 16 MiB archive limit, while the API's default HTTP body ceiling remains 6,000,000 bytes. A configured host can use a larger transport ceiling within its codec/storage policy.

Available reads are:

- `GET /package-imports?limit=50`: list metadata, newest revision first. Use `next_before_revision` as the exclusive `before_revision` cursor for the next page (limit 1–100).
- `GET /package-imports/{operation_id}`: read the immutable receipt.
- `GET /package-imports/{operation_id}/manifest`: read the inspected manifest.
- `GET /package-imports/{operation_id}/archive`: download the first accepted archive with no-store/nosniff headers, fixed attachment filename `nexus-flow-project.zip` and `X-Package-Digest`.

Reads require fresh permission and remain organization/environment scoped. Missing records return 404. Corrupted stored manifests/files or unavailable storage return sanitized 503 responses. A manifest or receipt is not a trust certificate; no archive is extracted, installed or executed.

Generic hosts must configure the import repository explicitly. The local demo configures persistent `package-imports.db` automatically when opening its backend; existing installations do not need reinitialization. Preserve this file with the rest of the installation during backup/recovery. Staged imports survive server restart and remain independent of current drafts, releases and active runs.

Target release materialization, artifact trust, configuration/worker readiness, activation of imported packages, retention/deletion and Admin UI are not available in this increment. Existing activation endpoints continue to accept only existing target releases; a staged package ID is not a release ID.
