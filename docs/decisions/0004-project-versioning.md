# ADR 0004: Project revisions and release core

Status: accepted for the local backend increment.

Use a Python service with authorization, validation, artifact-catalog and repository protocols. SQLite is a durable local metadata provider, not the production shared database. Each operation includes organization/environment/project scope. Authoring and publishing are Dev-only; importing and activating in UAT/Prod belong to Feature 09. The identity adapter verifies the token and current server grants on each operation. Project-specific ACLs are not implemented yet.

Store canonical JSON strings at repository boundaries so returned nested dictionaries cannot mutate historical snapshots. Save uses an expected revision and a transaction; restore appends a revision whose parent is the current head. Revision summaries and trusted author IDs are recorded. Structural comparison uses JSON pointers, treats arrays as whole values and optionally includes top-level canvas layout.

Publication commits the complete source revision, resolved pinned activity closure, both digests and the idempotency key in one transaction. A retry returns the original release even if validators/catalog availability changed. A reused key with a different source revision conflicts. Deletion preserves a durable key tombstone. The content digest covers all project content plus resolved artifacts. The executable digest excludes top-level canvas placement and test scenarios; it retains form layout/rules. Resource collections are sorted by ID for executable hashing; workflow node/edge array order remains significant. This is deterministic JSON serialization, not RFC 8785 canonicalization.

Release references are internal repository operations. Deployment/runtime adapters must acquire a reference before using a release and release it only after retention ends. Deletion checks references transactionally. No deployment or execution integration is claimed by this increment.

Builtin validation checks foundation graph integrity, duplicate IDs, explicit resource references and prohibited credential/environment value keys. JSON schemas are declarations and are excluded from the heuristic key scanner. It is not DLP: arbitrary secrets, schema defaults and expression literals need stronger policy in later authoring/catalog adapters. Full form rule compilation, runtime semantics and connector validation remain Feature 03/04 work. Validation and dependency resolution never import or execute Python activities.

The current surface is a library service and schema exporter. HTTP transport, React screens, production migrations/database provider and live activity catalog adapters are separate increments. The relevant UI handoff gate still applies.
