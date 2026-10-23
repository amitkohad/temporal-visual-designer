# ADR 0001: Python foundation and React browser UI

Date: 2026-10-23. Status: accepted for the foundation implementation.

The user explicitly selected Python for the technology stack and React for the browser UI. Python owns backend services, shared contract definitions, Temporal runtime and activity workers. React owns Designer, Admin and task-portal browser experiences. This foundation does not create UI code or select its graph/form library.

Use Python 3.12–3.13, uv with a committed lockfile, Pydantic 2 for strict typed contracts, and JSON Schema 2020-12 export for React and other consumers. Use jsonschema to check embedded data schemas and portable export conformance; never fetch user schema references. pytest supplies isolated tests; Ruff supplies lint/format; mypy checks production Python; Hatchling builds the package. pyproject.toml bounds versions and uv.lock pins the resolved environment. The earlier temporary TypeScript foundation was removed before any implementation source was written.

Freeze only the v0.1 foundation subset of C01/C02/C03/C06: environment scope/context, errors, activity descriptors/references, workflow/project definitions and step events. This is not approval or completion of the entire platform contract set. Structural checks reject extra fields, unknown versions and type coercion. Semantic checks cover node/port references, duplicates, start/terminal reachability and graph cycles. Expression evaluation, branch dataflow types and structured parallel-join semantics remain Designer/runtime work. Use validate_contract for the combined checks; exported schemas describe structure only.

Application shells are composition boundaries, not runnable web services. API framework, React build/graph/form choices, Temporal SDK and interpreter proof, production Entra topology, sandbox/build format, retention and sizing remain explicit adapter-specific gates. They do not block local schema tests. No Azure, live identity, external effect or Temporal SDK dependency is added here. Fakes remain test-only and do not establish authentication. Frozen Pydantic fields are not deep immutability; adapters copy snapshots.

Future Python modules use snake_case importable names rather than numbered/hyphenated specification directory names. Shared manifests/contracts/CI have one owner; independent feature agents own their package and acceptance tests. See nexusflow/modules/README.md.

Sources: https://docs.pydantic.dev/latest/concepts/json_schema/, https://docs.astral.sh/uv/guides/projects/, https://docs.pytest.org/en/stable/how-to/usage.html. Reproduce with uv sync --locked; tests never install dependencies at runtime.
