# ADR 0005: Offline authoring graph and mapping validation

Status: accepted for the backend validation increment.

Compose `AuthoringValidator` from existing publication checks, structured graph checks and catalog-backed activity input checks. Inject it into `ProjectService` through the existing ContentValidator port. This increment supplies a library adapter; there is no HTTP service, React canvas, form editor or default application composition yet.

Normal edges define decision/parallel splits and structured joins. Every split output must be connected once. Parallel paths remain separate until one matching join, including nested parallel blocks. Reusable workflow references are project-local and acyclic. Foundation validation still owns reachability, terminal paths, missing ports and graph back-edges. Error recovery crossing parallel regions is rejected unless it terminates in an explicit fail block; richer recovery semantics remain open.

Variable selection uses the reserved input-field envelope `{"$binding":{"schema_version":"0.1","source":"input","path":"/employee"}}`. A step source additionally requires `node_id`. Paths are JSON Pointers through explicitly required object properties; arrays and optional source paths are not proven. Each binding occupies one complete activity input field. Nested bindings or extra envelope keys fail validation. Ordinary strings and other literal JSON remain data, never Python or HTML.

This is a deliberately small variable-reference contract, not a general expression language. Conditions, arithmetic, transformations, loop variables and form expressions remain unfrozen. The proof.v1 runtime explicitly rejects reserved binding syntax instead of passing it to an activity as a literal. Runtime evaluation/conformance must be implemented and versioned before these bindings can execute.

Activity descriptors must exactly match ID/version/digest. Resolve schemas offline: reject `$ref` and `$dynamicRef` before instance validation. Literal inputs use JSON Schema 2020-12 and available local format checkers. With bound inputs, accept a simple object input schema, check required/extra fields and literal fields, and conservatively prove bound type compatibility. Identical source/target schemas are compatible; otherwise allow unconstrained target primitive types, including integer to number widening. More complex target constraints require matching schemas and are rejected when implication cannot be proven. No claim is made to a complete JSON Schema implication engine or every optional format checker.

A producer must strictly dominate the consumer across normal and error paths, and no producer failure path may reach that consumer. This prevents branch-only, future/self and failed-step output use. Some valid parallel-join output uses are conservatively rejected; an execution-aware dataflow contract is later work. Validation never executes/imports activity code or performs network schema resolution.

Forms, connector configuration, general conditions, executable workflow semantics and templates are outside this increment. Publication remains incomplete production validation until the remaining compiler/catalog policies are wired. Catalog providers are trusted adapters; the fake providers in tests establish no production trust guarantees.
