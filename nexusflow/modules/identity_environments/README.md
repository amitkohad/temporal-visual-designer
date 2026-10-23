# Identity and organization environments

This feature-01 backend core supplies:

- EntraTokenVerifier: signature/issuer/audience/lifetime/scope/client validation with server-injected signing keys and fresh directory groups.
- SQLiteIdentityRepository: persistent bindings, memberships, revisioned grants, logical Dev/UAT/Prod placement and transactional audit.
- AuthorizationPolicy: scoped server roles, user/group/workload separation and trusted placement.
- IdentityService: token-to-context/placement composition with bounded local admission.

Run `uv run pytest nexusflow/tests/identity`. Tests use real local RSA signatures, synthetic directory responses and temporary SQLite files. No credentials, live Microsoft service or cloud namespace is needed.

Only IdentityService should accept tokens at future request boundaries. Never construct a Principal or RequestContext from browser claims. Inject keys/groups from trusted server configuration. For group-based access, provide GroupResolver; missing/failed group resolution denies access. Unknown organizations and malformed tokens use the same unauthenticated gateway failure.

Binding configuration requires organization, canonical Entra tenant ID, API audience, delegated scope and explicit allowed user/workload client IDs. Workloads require application-only access tokens and their own membership/grant. Local grants use Entra object IDs; context actors retain signed subjects. Bootstrap repository changes are trusted internal operations and must be protected by future administration APIs.

SQLite persists metadata only. `provision_environments` creates logical placement records, not Temporal namespaces or Azure resources. Namespace/task-queue destinations come from stored routing, never client arguments. The current provider is local; shared production persistence and migrations remain planned. Tokens, keys and business payloads are not persisted in the audit log.

No React login/admin screen, online Microsoft adapter, full API authorization wiring, distributed limiter, project ACL or human-task eligibility service is implemented here. See [ADR 0003](../../../docs/decisions/0003-identity-environments.md).
