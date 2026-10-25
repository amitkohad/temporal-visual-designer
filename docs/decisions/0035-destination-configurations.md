# ADR 0035: Immutable destination configuration snapshots

Status: accepted for the local backend increment.

Materialization and activation already pin a configuration revision but previously consumed only an injected compatibility port. Supply an explicit SQLite provider for immutable, project-scoped destination configuration snapshots. This provider validates actual stored values against the release configuration schema.

## Identity and transaction

The key is organization, environment, project and caller-selected configuration revision. First creation atomically stores canonical JSON, a digest, verified actor/time and a project-local sequence. Exact canonical-value retries preserve the original receipt; changed values under the same revision conflict. A project need not exist yet, allowing configuration preparation before package materialization. Revisions are permanent in this increment; no overwrite/delete/rotation operation is introduced.

The default project quota is 1,000 revisions; exact retries precede quota checks. Values are bounded to 32 KiB, depth 16 and 1,000 aggregate container entries. Reads used for compatibility verify stored integrity. Lists select metadata only and use bounded exclusive sequence pagination. Existing project, activation and package tables do not change.

## Administration and readiness

All administration APIs require fresh `connection.manage` using the existing organization-administrator role. No business-author or production-support grants are broadened. Responses expose only receipt metadata and digest; stored values are private to the provider. Compatibility lookup is an internal trusted port used by existing materialization and activation authorization paths.

Compatibility checks validate bounded local JSON Schema against the actual immutable object. Reject reference keywords recursively so validation cannot resolve remote resources. Missing/incompatible revisions return false. Storage failure or corrupt snapshots report provider unavailability rather than claiming compatibility. The same provider instance supplies storage and compatibility in an explicitly composed host.

These snapshots accept non-secret configuration values only. They are not connection bindings, a secret manager, secret references, or a runtime value-injection mechanism. Configuration values stay outside project packages and workflow history; existing execution binding pins their revision identity. No production secret resolver or live Entra acceptance is implied. The fixed local-demo `local-v1` provider remains unchanged; generic hosts opt into this persistent provider explicitly.
