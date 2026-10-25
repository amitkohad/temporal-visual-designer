# Destination configuration revisions

A configured host can use `SQLiteConfigurationRepository` as its existing `ApiBackend.configurations` provider. This makes materialization and activation validate stored configuration values instead of a compatibility fixture. Preserve its database during backup/recovery. The standalone local demo still uses its existing fixed empty `local-v1` configuration.

An organization administrator with target-scoped `connection.manage` prepares a revision before importing a project:

```http
PUT /projects/hr/configurations/uat-config-v1
Content-Type: application/json

{"values": {"approval_region": "west"}}
```

Use non-secret configuration only. Secret values and connection credentials belong in a future environment binding/secret provider. Configuration is stored outside exported project packages and is not copied into workflow history or injected into workflow inputs by this feature.

The response is immutable metadata: `project_id`, `configuration_revision`, project-local sequence `revision`, `values_digest`, original actor and timestamp. Exact retries return this original receipt; changed values under the same revision return 409. Choose a new revision ID to change configuration. A target project need not exist yet. No activation is performed.

The following routes require the same fresh administrator permission and return metadata only:

- `GET /projects/hr/configurations/uat-config-v1`
- `GET /projects/hr/configurations?limit=50&before_revision=100`

Use `next_before_revision` for the next page. The default quota is 1,000 immutable revisions per scoped project. Values are limited to 32 KiB, depth 16 and 1,000 aggregate container entries. Configuration identity is isolated by organization, environment and project.

Supply `uat-config-v1` as `configuration_revision` in [materialization](package-materialization.md) and activation requests. Readiness validates its stored object against the selected release schema. Missing/incompatible configuration or schemas requiring reference resolution cannot pass readiness. Provider failures/corruption return sanitized unavailable errors. Configuration compatibility is rechecked at activation, including rollback; existing running executions keep their original revision identity.

This increment supplies persistence and local schema compatibility. Admin UI, actual connection-resource mappings, secret resolution/rotation, runtime consumption of values and production deployment providers remain separate work.
