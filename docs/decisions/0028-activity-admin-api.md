# ADR 0028: Activity administration API

Status: accepted for the local HTTP/provider increment.

Expose the existing private source-bundle intake and published catalog lifecycle through signed, organization/environment-scoped HTTP routes. This supplies backend operations for a future Admin portal without changing the activity format, role policy, worker capabilities or execution model.

`POST /activity-imports` accepts raw `application/zip` bytes. It avoids multipart parsing, temporary extraction and base64 expansion. The existing API boundary buffers requests with a default 6,000,000-byte limit before routing; the domain archive maximum remains 8 MiB. The effective limit is the smaller configured limit. After the scoped session is established, check `activity.import` before inspecting media type or processing the archive. ZIP validation and persistence run outside the event loop. Intake does not install, import or execute Python.

Return typed import metadata with the normalized manifest, validation state and trusted actor/timestamp. Private metadata and archive downloads require fresh `activity.import`; ordinary palette readers cannot inspect imported candidates. Download returns the first accepted original archive as `application/zip` with a fixed attachment filename. Existing no-store and correlation headers apply to binary responses as well as JSON errors. This is a bounded complete download, not a streaming or range API.

Publication accepts only an immutable artifact digest and uses the existing mandatory capability provider. A validated import does not become discoverable until publication succeeds. Published lookup accepts a complete activity reference, including digest, and returns its lifecycle metadata even if withdrawn. New-use resolution still rejects withdrawal through the existing domain resolver. Lifecycle transitions use expected-generation concurrency; deprecated versions remain pinned-readable and withdrawn versions leave search results. The API exposes no deletion or caller-supplied retention references.

Retain domain idempotency: logically identical import retries keep the first archive and actor, repeated publication keeps the original entry, and exact lifecycle retries return the existing generation according to the domain contract. Recheck authorization for every retry. Strict JSON bodies cannot supply actor, scope, capabilities or arbitrary source content. Use the existing sanitized error envelope.

No role grants are added. Existing scoped activity publishers can operate wherever explicitly granted by identity policy; package promotion rights alone do not grant activity publication. The synthetic local launcher has no activity-publisher grant by default. Production host provisioning and capability approval remain explicit host responsibilities.

Admin/Designer React screens, live Entra providers, custom Python worker isolation and whole-project package transfer remain open. HTTP intake and fake capability tests do not establish executable worker support or production identity acceptance.
