# ADR 0038: Explicit-public frozen workflow input inspection

Status: accepted for the bounded Dev debugger backend increment.

Add read-only inspection of the workflow inputs already pinned in a debug session. This is the first inspection subset; it does not query activity outputs, step payloads, variable state or a secret/connection provider.

## Binding and authorization

Every request reuses fresh debug.execute, explicit Dev, original-owner and persisted placement/session integrity checks. Load only the original project revision and selected workflow, verify scope/revision and the exact proof definition digest before interpreting its schema. Never use the current draft schema to declassify an older session. If the original provider/snapshot is missing or inconsistent, return a sanitized unavailable error. The worker need not be running and a completed run can still be inspected while its retained source/session records remain available.

## Fail-closed projection

Only present primitive fields explicitly annotated `x-nexusflow-inspect: true` may be exposed. `writeOnly: true`, `x-sensitive: true`, `x-secret: true` and `format: password` override public annotations and hide the whole subtree. Object containers allow traversal of declared properties, not blanket child disclosure. Unsupported/ref/composition/conditional schemas, arrays, unclassified fields and unknown dynamic keys remain hidden. Unknown key names must not leak through redaction metadata; report the nearest declared parent pointer instead.

This annotation describes visibility for caller-supplied workflow inputs, not permission to resolve environment secrets or read runtime payloads. Authors must classify public fields deliberately. Future secret/runtime adapters retain their own independent trust boundaries.

## Budgets and compatibility

Return only session identity, workflow/source revision/digest, a sanitized values object, bounded deterministic JSON Pointer redaction paths and a truncated flag. Bound the entire response to 16 KiB, each disclosed scalar to 1,024 JSON bytes, traversal to depth eight/1,000 visited nodes and redaction metadata to 100 paths. Omit data on budget exhaustion and report truncation; never return raw protected values in failures. Inspection is pure/read-only after authorization and source verification, creates no command receipt and cannot release a debug gate or alter activation.

Existing schemas without explicit annotations expose no unclassified values. No persisted session/plan/start/workflow formats or dependencies change. The opt-in local debugger receives the route automatically. Browser inspector controls, arrays/composed schema support, step inputs/outputs, runtime variables, events and broader debugger lifecycle remain separate work.
