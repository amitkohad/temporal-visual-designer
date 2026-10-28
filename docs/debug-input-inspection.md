# Inspect frozen workflow inputs

An enabled Dev debugger now exposes:

```http
GET /projects/{project_id}/debug-sessions/{session_id}/inputs
```

The route requires fresh debug.execute and original session ownership. It returns `session_id`, `workflow_id`, `source_revision`, `definition_digest`, sanitized `values`, `redacted_paths` and `truncated`. It reads the saved input and the exact original revision schema, so editing the current draft does not change visibility in an older session. It works while the worker is unavailable or after the run completes; it does not advance the workflow. The session must first have an acknowledged dispatch; recover a pending intent through its original create request.

Disclose only deliberately public primitive fields in the workflow input schema:

```json
{
  "type": "object",
  "properties": {
    "employee_name": {"type": "string", "x-nexusflow-inspect": true},
    "token": {"type": "string", "writeOnly": true},
    "notes": {"type": "string"}
  }
}
```

For inputs containing all three fields, only `employee_name` appears in `values`. Protected annotations override public annotations. `writeOnly`, `x-sensitive`, `x-secret` set to true and `format: password` hide a subtree. Containers do not expose all descendants; each primitive needs its own public annotation. Arrays without the new explicit array opt-in, composed/conditional/reference schemas, unclassified values and undeclared dynamic keys remain hidden. See [homogeneous array inspection](debug-array-inspection.md) for the additional marker, item classification and position/budget rules. Unknown input key names are omitted from redaction paths too.

The entire response is bounded to 16 KiB. Oversized scalars (over 1,024 JSON bytes), depth beyond eight, traversal beyond 1,000 nodes or redaction metadata beyond 100 paths are omitted and set `truncated`. A redacted JSON Pointer identifies a declared hidden field or parent, not a secret value. No activity outputs or step input payloads are returned. No connection credentials or secret references are resolved.

For a local example, [enable the debugger](local-development.md#enable-dev-debugging), create [the sample project](../examples/local-debug-input-project.json) through `POST /projects`, then start a session using workflow `inspect-inputs`, revision 1, configuration `local-v1`, explicit `mode: real` and inputs such as `{"employee_name":"Alex","token":"private-example","notes":"hidden"}`. Read the new inputs route on the returned session ID. No publication/activation is required. Preserve the original source and session stores during recovery.

Use [the session controls](designer-debug-sessions.md) separately to step or continue. Invalid/missing source binding or unavailable storage returns a safe error rather than applying a newer schema. Browser controls, runtime variables, outputs and broader redaction policies remain planned.
