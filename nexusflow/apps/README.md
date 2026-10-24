# Application boundaries

Python owns the API/control plane, shared contracts, Temporal runtime and activity workers. React owns browser UI for Designer, Admin and the task portal. Implemented application roots include local contract validation/export commands and an explicitly configured FastAPI application factory.

`api.create_app(ApiBackend(...))` composes authenticated project/catalog/activation/start/status routes and exports guarded OpenAPI. `api_backend.TemporalRuntimeGateway` bridges trusted environment placement to actual SDK clients. See `docs/api.md` for hosting/testing and remaining scope. A turnkey bootstrap/launcher, deployed identity/provider composition, isolated Python activity worker and React application are still pending. React will consume exported JSON Schema 2020-12 and API contracts; it must not maintain competing authoritative workflow models. UI implementation follows the approved Figma handoff.

Application roots may import feature modules and contracts. Modules may import contracts and their own internals; cross-feature behavior uses injected ports. Contract code cannot import modules, applications, test fakes or cloud SDKs. Production roots must never import test helpers. Root manifests, CI and composition changes have one integration owner.
