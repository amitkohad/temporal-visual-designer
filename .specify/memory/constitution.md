# Nexus Flow Designer specification constitution

Version: 1.0.0 | Adopted: 2026-10-23 | Scope: Nexus Flow Designer application specifications.

1. Specify user outcomes, acceptance tests, contracts, plans, and traceable tasks before implementation. This change authorizes documentation only.
2. Temporal is the durable execution foundation. Keep application development outside Temporal server internals unless a separately reviewed requirement proves necessary.
3. Treat organizations and Dev/UAT/Prod environments as authorization and data boundaries. Authenticate through Azure Entra ID; enforce permissions server-side.
4. Publish immutable whole-project releases. Pin definitions, activities, forms, and runtime compatibility for each execution. Activation and rollback affect new starts only.
5. Promote identical artifacts. Keep secrets and environment configuration outside exported projects. Production evidence is optional; deployment audit is mandatory. Do not implement a change-request approval system.
6. Make every feature independently testable through versioned contracts and deterministic fakes. Verify contracts again against real providers during integration.
7. Enable parallel agents through disjoint file ownership, explicit prerequisite task IDs, and contract freeze gates. Shared files have one owner. Never mark dependent tasks parallel.
8. Preserve application portability. Azure is the first target; do not claim adapters or capacity guarantees that have not been validated.
9. Develop Nexus Flow Designer from scratch in this repository. Keep application architecture and delivery plans self-contained, with Temporal accessed through supported client interfaces. These specifications do not authorize implementation or infrastructure provisioning.
10. Follow repository code guidelines and testing guidance when future implementation is authorized. Application test commands and integration-suite conventions must be selected for this repository; Temporal server test tags are not inherited. Select new dependencies explicitly before implementation.

11. Design the business-user experience in Figma before UI implementation. Keep editable artifacts, reusable components, versioned handoff references and testable usability/accessibility criteria. Static concepts are not proof of working interactions or compliance; shared design assets have one owner during parallel work.
