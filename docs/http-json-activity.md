# Typed HTTP JSON activity

The internal HTTP JSON adapter accepts business configuration and returns only
status metadata. It uses the existing credential-free live executor or explicitly
selected Dev mock executor; it is not registered in Temporal, the local launcher
or an API/palette.

Business inputs contain `connection_id`, `connection_revision`, `execution_mode`,
`path`, optional `method` (default GET), `json_body` and `fixture_digest`. All fields
live in the descriptor's input schema because current authoring does not validate
a separate node configuration slot. Scope and operation ID are trusted host
arguments; credentials, arbitrary headers and context fields are rejected.

```python
adapter = HTTPJSONActivityAdapter(live=live_executor, mock=mock_executor)
output = await adapter.execute(
    scope,
    "run-step-1",
    {
        "connection_id": "hr-api",
        "connection_revision": "v1",
        "execution_mode": "live",
        "method": "POST",
        "path": "/employees",
        "json_body": {"employee": "example"},
    },
)
```

Mock mode requires a selected fixture digest and passes through trusted Dev
authorization. Live mode forbids that pin. No provider fallback occurs. GET/HEAD
have no body; other methods may provide a JSON object encoded canonically as
UTF-8. An omitted/null `json_body` sends no bytes. Bodies are limited to 1 MiB,
depth 32 and 10,000 nodes; cycles/shared mutable aliases, non-finite or unsupported
values fail before invoking a provider.

Output has only `operation_id`, `status_code` and `execution_mode`. Raw response
headers/body are discarded, including sensitive provider values. HTTP status
errors and redirects remain statuses; there is no automatic retry or redirect.
Caller-provided JSON can contain secrets: input privacy is not automatic secret
detection, safe package export or configurable redaction. Response mapping and
general variable bindings remain future work.

`http_json_activity_descriptor()` and `HTTPJSONDescriptorCatalog` expose fresh
typed metadata for offline literal authoring. `http_json_activity_artifact()`
returns the canonical metadata bytes matching its reference digest. These are
contract bytes, not executable Python code. The catalog intentionally has no
artifact resolver; current project publication remains blocked until executable
built-in artifact/capability support is implemented. It is not a deployed palette
entry merely because a descriptor exists.

The portable schema validates field/mode combinations, while backend validation
also enforces full paths and body structure/byte bounds. The adapter validates
provider results, has a bounded provider deadline up to 30 seconds after input
normalization, propagates cancellation and reports private closed errors without
automatic retries. Unknown live failures may have delivered a request; mock
failures have no external delivery. See [ADR 0054](decisions/0054-http-json-activity.md).
