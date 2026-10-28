# Inspect a completed activity output

Use the saved session and a completed activity node:

```http
GET /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/steps/notify/output
Authorization: Bearer TOKEN
```

The response identifies the session, source revision, definition digest, node and
pinned activity reference. `values` contains the public projection;
`redacted_paths` and `truncated` describe omitted values and limits. The response
uses `Cache-Control: no-store`.

Activity authors classify fields in the activity descriptor's `output_schema`
with `x-nexusflow-inspect: true`. Protection flags and password format take
precedence. Arrays require both that flag and `x-nexusflow-inspect-array: true`,
with separately classified items/leaves. Unknown fields are hidden without
disclosing their names. Use a new activity version and project revision to
change classification; existing sessions keep their pinned schema.

Only completed activities with JSON object results are supported. Timer,
approval, pending, in-flight and failed activity attempts have no inspectable
output here. Missing schema, mismatched digest or unavailable runtime queries
return safe errors. Reads require current Dev permission and original ownership,
including after completion. They never advance the workflow.

The existing 64 KiB raw-result limit applies. Public projection uses scalar,
array, depth and traversal limits and a 16 KiB complete-response cap. Inspection
may return an empty projection with root redaction/truncation when limits apply.
See [ADR 0044](decisions/0044-debug-step-output-inspection.md) for the precise
scope. General variables, scalar roots and browser controls remain open.
