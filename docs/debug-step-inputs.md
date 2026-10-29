# Inspect a saved activity input

Use an acknowledged debug session and one of its activity node IDs:

```http
GET /api/v1/organizations/acme/environments/dev/projects/hr/debug-sessions/SESSION_ID/steps/notify/input
Authorization: Bearer TOKEN
```

The response identifies the session, source revision, definition digest, node and
pinned activity reference. `values` contains only fields classified public by
that activity version's `input_schema`; `redacted_paths` and `truncated` describe
omissions and limits. Responses use `Cache-Control: no-store`.

This shows the saved literal input mapping that the proof interpreter supplies
to the activity. It is separate from the workflow input inspector and does not
merge workflow inputs, resolve secrets or evaluate dynamic expressions. A later
draft or activity version cannot change an old session's inspected values or
classification. Create a new session or rerun to use a new revision.

Inspection works before the activity runs, while its worker is offline and after
completion. It does not query runtime state or advance the workflow. Current Dev
permission and original ownership are required for every read. Pending session
dispatch must first be acknowledged by retrying its original start request.

Activity authors opt in fields with `x-nexusflow-inspect: true`. Protection flags
and password format take precedence; unknown fields remain hidden. Arrays need
both that flag and `x-nexusflow-inspect-array: true`, with independently classified
items/leaves. The existing traversal and scalar limits apply, with a 16 KiB
complete-response cap. Missing schemas or inconsistent source/plan/digest data
return safe errors. See [ADR 0045](decisions/0045-frozen-activity-input-inspection.md).

Live variables, dynamically resolved inputs, attempt inspection and browser
controls remain open.
