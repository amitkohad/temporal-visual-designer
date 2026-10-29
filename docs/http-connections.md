# Register an HTTP connection revision

An explicitly configured host can provide an environment-scoped HTTP connection
registry to organization administrators with `connection.manage`:

```http
PUT /api/v1/organizations/acme/environments/dev/connections/hr-api/revisions/v1
Authorization: Bearer TOKEN
Content-Type: application/json

{
  "connector_type": "http",
  "connector_version": "1.0.0",
  "base_origin": "https://api.example.com",
  "allowed_origins": ["https://api.example.com"],
  "secret_reference": "hr-api-credential"
}
```

Use a new revision label for each definition change. An exact retry returns the
original metadata; changing a definition under an existing label conflicts.
Canonical equivalent origins count as the same definition. Defaults allow 100
connection IDs per organization/environment and 100 revisions per ID.

Read metadata or revision history:

```http
GET /api/v1/organizations/acme/environments/dev/connections/hr-api/revisions/v1
GET /api/v1/organizations/acme/environments/dev/connections/hr-api/revisions?limit=25
```

History is descending by revision; follow `next_before_revision` through the
exclusive `before_revision` parameter. Limits are 1–50. Public responses include
the definition digest and `has_secret_reference`, not origins or the reference
label. Responses use `Cache-Control: no-store`. Permissions are rechecked for
every request, including retries and reads in other environments.

Origins must be HTTPS DNS origins with no credentials, non-root path, query or
fragment. Default port/root slash and case are normalized; duplicate canonical
origins and IP literals are rejected. No DNS lookup, outbound request or secret
resolution occurs. Use reference labels rather than credential values; existence
and authorization of those labels will be checked by future execution adapters.

Hosts must inject a `SQLiteConnectionRepository` through `ApiBackend.connections`
and own its lifecycle. Preserve the whole database for recovery. The default
local launcher does not enable a registry or add administrator grants. This
increment does not make an HTTP activity executable or include connection
definitions in exported projects. See [ADR 0049](decisions/0049-http-connection-registry.md).
