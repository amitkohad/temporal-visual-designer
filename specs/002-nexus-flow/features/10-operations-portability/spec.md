# Feature 10: Application operations and portability

## Scope and requirements
Application isolation, capacity controls, observability, recovery, and provider boundaries for shared services. Shared contracts: [contracts.md](../../contracts.md).

- F10-FR01: Route organization/environment work using trusted namespace, queue, configuration, and artifact placement. Reject caller-supplied scope overrides.
- F10-FR02: Configure per-scope admission/concurrency budgets, explicit queue/rejection status, and independently scalable worker pools; support future dedicated placement.
- F10-FR03: Expose readiness, draining, redacted logs/metrics/traces with execution/release correlation and bounded metric cardinality.
- F10-FR04: Keep domain interfaces independent of cloud-specific secret, artifact, identity-verification, and Temporal connection implementations. Azure is the first intended host.
- F10-FR05: Graceful drain and restart preserve accepted durable executions. Document recovery responsibilities for definitions, artifacts, tasks, and activations.
- F10-FR06: Retain release dependencies for running executions; diagnose incompatible placement instead of silently migrating runs across clusters.
- F10-FR07: Record benchmark conditions and unresolved sizing. Do not invent throughput, tenant, availability, RTO, or RPO commitments; obtain targets before production acceptance.

## Acceptance scenarios
- F10-A01 (positive/negative, FR01): Given two organizations and their environments, when valid requests and hostile overrides arrive, then only configured authorized queues/namespaces receive work.
- F10-A02 (positive, FR02): Given one scope exhausts its budget, when another submits work, then the second is independently admitted under its own limits and the first sees explicit status.
- F10-A03 (recovery, FR05/06): Given accepted work during drain/restart, when workers return, then execution resumes with pinned dependencies and no silent loss.
- F10-A04 (positive/negative, FR03/04): Given fake provider adapters, when a fixture executes, then domain contracts need no Azure SDK and diagnostics correlate work without secret fixtures.
- F10-A05 (negative, FR06): Given placement changed for new work, when an old run resumes, then it retains compatible routing or reports unsupported migration; it never silently moves clusters.

## Interfaces and success
Own PlacementResolver, AdmissionPolicy, ReadinessReport, DrainStatus, and provider conformance fixtures. Consume execution/release/identity contracts. All five scenarios pass against fake queues/providers independently; disposable Temporal verifies restart integration. Performance reports state measurements and conditions, with production acceptance pending approved sizing/recovery targets.

## Deployment boundary
These specifications define application operations and portability requirements only. Azure resources, infrastructure configuration and deployment pipelines require a separate reviewed implementation plan for this project. Running-execution cluster migration is deferred.
