# ADR 0027: Project history API

Status: accepted for the local backend increment.

Expose existing immutable revision capabilities through signed, scoped HTTP endpoints before building React history screens. History uses metadata-only SQL with descending exclusive revision cursors and a maximum page of 100. This avoids loading snapshot bodies and offset drift under concurrent saves.

Comparison uses canonical JSON Pointer changes, excludes top-level layout by default and preserves absent-versus-null distinctions. Bound output to 1,000 changes and 262,144 UTF-8 bytes of canonical change objects. Abort without partial results when either budget is exceeded. Existing internal unbounded comparison remains compatible.

Restore appends a historical snapshot with optimistic expected-head concurrency and trusted server identity. It is restricted to Dev and has no publication, activation or runtime side effects. Lost-response retries must reload; restore is not an idempotency-key command.

History and comparison recheck project.read; restore rechecks project.write. All lookups remain organization/environment/project scoped and reuse sanitized API errors. No schema migration or dependency changes are required.

React screens, package promotion and hosted identity remain separate work. Local signed-token tests do not establish production Entra acceptance.
