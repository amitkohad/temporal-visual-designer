# Product specification: Nexus Flow Designer

Status: Draft for review | Date: 2026-10-23 | Priority: P1.

## Outcome and users

Business users author business processes visually, test them in Dev, and promote whole-project releases through their organization's UAT and Prod environments. Temporal supplies durable execution. Developers author Python activities externally; administrators import and publish them. Production support operates production releases through Admin.

## User stories and independent acceptance

### US1 — Author a business process (P1)
Given a Dev project and published activity fixtures, when a business user builds a form-triggered purchase request with amount routing and approval, then validation identifies missing mappings and saving creates a restorable revision. Independent test: Designer with fake catalog, identity, and project APIs.

### US2 — Execute and debug (P1)
Given a frozen Dev revision, when a user steps through a test run, then inputs/outputs and variables are inspectable at durable boundaries; restarting a worker does not lose progress. Unauthorized environment access is denied. Independent tests split debugger event fixtures from runtime restart tests.

### US3 — Complete human work (P1)
Given tasks assigned to Entra users/groups, when eligible users approve, reject, delegate, or supply information, then configured approval rules advance the process exactly once at the application transition boundary. A duplicated delivery cannot advance it twice. Independent test: fake directory, clock, and workflow gateway.

### US4 — Promote a release (P1)
Given a valid project package, when an authorized business user imports and activates it in UAT, then the exact package digest becomes active for every included workflow. Only production support may import/activate in Prod. Optional evidence may be omitted without blocking deployment. Independent test: fake registry and runtime routing.

### US5 — Extend the palette (P1)
Given an externally authored Python activity bundle, when an administrator validates and publishes it, then its schema-generated configuration becomes available in the permitted palette. Invalid or incompatible artifacts cannot become executable. Independent test: synthetic bundles and worker adapter.

## Product requirements

- NF-001: Provide standalone Designer, Admin, and task portal experiences.
- NF-002: Every organization has isolated Dev, UAT, and Prod environments, initially on shared services.
- NF-003: Entra authentication and environment/project permissions apply to every API, event stream, artifact, and task.
- NF-004: Projects contain multiple workflows, forms, activities, connection declarations, configuration, and tests; version control is built in.
- NF-005: Export/import the whole project as an immutable portable release; activate atomically project-wide.
- NF-006: Pin running executions to a release and compatible runtime/activity artifacts; rollback changes future starts only.
- NF-007: Provide the baseline starts, flow control, data, integration, reliability, and human capabilities enumerated in features 03–08.
- NF-008: Support basic step debugging exclusively in Designer/Dev with secret redaction.
- NF-009: Import external Python activities through Admin and expose approved versions in the palette.
- NF-010: Business users promote Dev/UAT; production support imports/activates Prod. Capture optional change number and approval uploads, mandatory immutable deployment audit.
- NF-011: Support portable application boundaries and future dedicated placement; infrastructure migration of active runs is outside initial scope.
- NF-012: Independently test every feature; integrate both reference processes before release.

## Acceptance reference processes

Employee onboarding: submit employee form → manager/user-group approval → parallel HR, IT, facilities tasks → join → completion notification. Verify rejection, missing information, delegation, reminder, escalation, duplicate response, and worker restart while waiting.

Purchase requisition: submit requester/items/amount/currency → validate → route by configured amount threshold and department → configurable approval stages → purchasing integration → requester notification. Verify below/at/above threshold, rejection, connector timeout and retry, duplicate start, unauthorized approver, and rollback during an in-flight request.

## Success criteria

SC-01: Both reference processes can be authored, versioned, tested, exported, imported, activated, and completed without editing workflow code.
SC-02: Every feature passes its isolated acceptance suite without unrelated live services; provider integration tests verify its fakes remain faithful.
SC-03: Tenant/environment negative tests deny every enumerated cross-boundary access route.
SC-04: Restart, redelivery, and concurrent activation scenarios preserve pinned releases and one accepted business transition.
SC-05: Every requirement maps to feature acceptance and a task; every parallel task has disjoint ownership and completed prerequisites.
SC-06: Capacity and recovery targets are measured and agreed before production readiness; no numeric throughput promise is assumed.

## Exclusions

No implementation, deployment, dependency installation, cloud mutation, Temporal server fork changes, live-run migration, arbitrary Python debugging, live definition editing, Git-based authoring, or in-tool change-request approval is authorized by this specification change.

## Product experience requirement

NF-013: Deliver a Figma-designed, business-user-first experience governed by [ux-design.md](ux-design.md): guided authoring, contextual insertion, keyboard alternatives, recoverable editing, progressive disclosure, responsive tasks/forms, accessible states and in-place debugging. Read [design-handoff.md](design-handoff.md) for actual artifacts and review status. SC-07: UX-A01–08 pass their documented prototype and subsequent implementation checks before UI release; no research or accessibility compliance is assumed from static designs.

The approved Designer visual baseline is spatial frame 5:14 (visual revision v2), recorded on 2026-10-23 in design-handoff.md. NF-013 includes compact icon activities, shape-based events/decisions, explicit labeled connectors, slim rails, contextual properties and zoom/minimap under UX-013/014. Approval of visual direction does not complete interaction or usability verification.
