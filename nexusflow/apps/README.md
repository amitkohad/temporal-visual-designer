# Application boundaries

Python owns the API/control plane, shared contracts, Temporal runtime and activity workers. React owns browser UI for Designer, Admin and the task portal. This foundation implements only local contract validation/export commands.

Future composition roots: API, Temporal workflow worker, isolated Python activity worker, and React browser application. No production auth, HTTP server, workflow worker or browser shell exists yet. React will consume exported JSON Schema 2020-12 and API contracts; it must not maintain competing authoritative workflow models. UI implementation follows the approved Figma handoff.

Application roots may import feature modules and contracts. Modules may import contracts and their own internals; cross-feature behavior uses injected ports. Contract code cannot import modules, applications, test fakes or cloud SDKs. Production roots must never import test helpers. Root manifests, CI and composition changes have one integration owner.
