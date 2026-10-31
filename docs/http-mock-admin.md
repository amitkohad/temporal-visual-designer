# Administer HTTP JSON mock fixtures in Dev

A host must explicitly supply `ApiBackend.mock_fixtures` using
`SQLiteHTTPMockScenarios` and enable selected scopes in
`ApiBackend.mock_fixture_dev_scopes`. Keep the provider open for the application's
lifetime and close it afterward. Existing default launcher behavior is unchanged.
Both endpoints require signed administrator `connection.manage` permission and
an explicitly enabled Dev scope. UAT and Prod are forbidden.

Under `/api/v1/organizations/{organization_id}/environments/{environment_id}`:

| Method | Path | Result |
| --- | --- | --- |
| PUT | `/connections/{connection_id}/revisions/{connection_revision}/mock-fixture` | 201 immutable fixture metadata |
| GET | `/connections/{connection_id}/revisions/{connection_revision}/mock-fixture` | 200 verified fixture metadata |

Write synthetic JSON fixtures, for example:

```json
{
  "method": "POST",
  "path": "/employees",
  "json_body": {"employee": "example"},
  "status_code": 201,
  "response_json_body": {"accepted": true}
}
```

Response metadata contains scope, connection ID/revision, `fixture_digest` and
`stored_bytes`. Use that digest with an explicitly selected HTTP JSON mock
activity. Raw fixture content cannot be downloaded through this API. GET loads
private fixture content internally to verify integrity before returning metadata.

Bodies must be JSON objects or omitted/null; null maps to empty bytes and `{}` to
the bytes of an empty JSON object. Each object is limited to 1 MiB of canonical
UTF-8 JSON, depth 32 and 10,000 nodes. GET/HEAD cannot have request bodies;
HEAD/204/304 cannot have response bodies. Headers and binary bodies are not
accepted by this subset. All query parameters and unknown fields are rejected.

Identical writes are idempotent. Different content at an existing key and scoped
storage quota exhaustion return 409; use a new revision for changed fixtures.
Missing fixtures return 404, forbidden scopes/permissions 403, unavailable
providers 503 and invalid fields 422. Existing unauthenticated handling applies.
No fixture write configures a live HTTP destination or starts a workflow.

See [ADR 0057](decisions/0057-http-mock-admin.md) and the
[private storage guide](persistent-http-mocks.md). Explicit hosts can also
[discover fixture metadata](http-mock-listing.md) through paginated listing.
UI, packaging and default launcher integration remain separate work.
