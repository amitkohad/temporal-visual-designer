# Feature module ownership

The ten numbered feature directories in specs identify ownership, not Python import names. Use snake_case Python packages for implementations: identity_environments, project_versioning, designer_forms, activity_catalog, durable_runtime, human_tasks, designer_debugger, connectors, package_promotion and operations_portability. Do not create empty feature classes as placeholders.

Each future feature owns its package, local adapters, migrations and isolated tests. Shared contract changes require coordinator review. Do not import another feature's private implementation. UI work belongs in the future React app with matching ownership boundaries. Shared navigation, manifests, lockfiles and schema export remain single-owner integration work.

Tests use provider protocols and deterministic fakes, then rerun the same conformance cases against real providers. This foundation's fake scoped repository proves test isolation only; it is not Entra authentication or production authorization.
