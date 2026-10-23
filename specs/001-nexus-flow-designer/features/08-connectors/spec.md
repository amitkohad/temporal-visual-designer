# Feature 08: Configurable connectors

## Scope
Business users configure separately versioned HTTP, email, and external-notification activities through schema-driven forms. This feature consumes the shared contracts in [contracts.md](../../contracts.md). Dedicated service connectors may follow the same contract; no unspecified service integration is promised.

## Requirements
- F08-FR01: Publish distinct HTTP, email, and notification activity descriptors with typed inputs, outputs, examples, and connection requirements.
- F08-FR02: Persist connection references scoped to organization/environment; resolve secrets only at execution. Packages, logs, debugger output, and activity descriptors must not expose secret values.
- F08-FR03: Validate configuration before execution; report actionable field errors. Reject unauthorized or cross-environment bindings.
- F08-FR04: Apply timeouts, cancellation, bounded retry policies, response size limits, and configured destination/network restrictions, including redirect targets. Classify validation and authorization failures as non-retryable.
- F08-FR05: Preserve a logical operation identifier across retries, expose connector-specific idempotency support, and report ambiguous outcomes. Do not promise exactly-once delivery for providers without deduplication.
- F08-FR06: Support deterministic mock connections in Dev; production configuration cannot silently fall back to mocks.
- F08-FR07: Redact credentials and configured sensitive fields from execution inspection and audit, including provider errors.

## Acceptance scenarios
- F08-A01 (positive, FR01/03/06): Given each descriptor and a mock provider, when a user configures and executes it, then typed results match the fixture and the three activities remain independently selectable.
- F08-A02 (negative, FR02/03/04): Given a cross-environment reference or forbidden redirect destination, when execution is requested, then it is denied without transmitting credentials or business payload.
- F08-A03 (recovery, FR04/05): Given a provider accepted an operation before a transport failure, when retry occurs, then the logical identifier is unchanged; a deduplicating provider records one effect, and a provider without support yields a documented ambiguous outcome policy.
- F08-A04 (negative, FR02/07): Given secret-bearing provider errors, when logs, export, or run inspection are read, then secret fixtures are absent.
- F08-A05 (recovery, FR04): Given a stalled provider, when cancellation or timeout occurs, then the attempt stops and subsequent processing follows the configured failure route.

## Data and interfaces
Own ConnectorDescriptor, ConnectionBinding metadata, ConnectorInvocation, ConnectorResult, and normalized ConnectorError. Consume ActivityDescriptor, SecretResolver, ExecutionContext, and authorization ports from shared contracts. ConnectionBinding stores secret references, not secret values. HTTP targets and email recipients have explicit schemas; notification adapters declare supported transport and destination policy.

## Independent verification and success
Run the feature suite against fake secret, identity, and provider ports without Designer, live email, Azure, or a production Temporal cluster. All five scenarios must pass for each applicable connector; zero secret-fixture leaks or cross-scope dispatches are permitted. Provider-specific end-to-end tests are opt-in and use disposable endpoints.

## Exclusions
No arbitrary Python activity import implementation (owned by the activity feature), cloud provisioning, or broad service-specific connector catalog.
