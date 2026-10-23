# Nexus Flow Designer specification set

Created: 2026-10-23. Status: implementation in progress. Python backend foundations and local acceptance suites are delivered; React UI, production adapters and full integration remain open. See task checkboxes and implementation records for verified scope.

This set follows the GitHub Spec Kit specification → plan → tasks structure. It is authored documentation, not a claim that the Specify CLI, agent commands, or generated scaffolding are installed. Process reference: https://github.com/github/spec-kit.

## Read order

1. [Product specification](spec.md).
2. [Architecture and delivery plan](plan.md).
3. [Shared contracts](contracts.md) and [data model](data-model.md).
4. [Parallel execution plan](tasks.md).
5. Feature-local spec.md, plan.md, and tasks.md below.
6. [Decisions and open questions](research.md), [validation guide](quickstart.md), and [quality checklist](checklists/requirements.md).
7. [Requirement traceability](traceability.md).

## Feature index

| ID | Feature | Primary independent test boundary |
|---|---|---|
| 01 | [Identity and environments](features/01-identity-environments/spec.md) | Fake Entra issuer and scoped repository |
| 02 | [Projects and versioning](features/02-project-versioning/spec.md) | In-memory revisions and artifact store |
| 03 | [Designer and forms](features/03-designer-forms/spec.md) | Browser against contract fixtures |
| 04 | [Activity catalog](features/04-activity-catalog/spec.md) | Imported synthetic Python bundles |
| 05 | [Durable runtime](features/05-durable-runtime/spec.md) | Temporal test environment and fake effects |
| 06 | [Human tasks](features/06-human-tasks/spec.md) | Fake directory, clock, and runtime delivery |
| 07 | [Designer debugger](features/07-designer-debugger/spec.md) | Fake runtime event stream |
| 08 | [Connectors](features/08-connectors/spec.md) | Local HTTP/mail/notification fixtures |
| 09 | [Package promotion](features/09-package-promotion/spec.md) | Fake registry, permissions, and routing |
| 10 | [Operations and portability](features/10-operations-portability/spec.md) | Synthetic telemetry and provider adapters |

Numbered paths under `nexusflow/` in plans identify ownership; implemented Python packages use snake_case module names as recorded in `nexusflow/modules/README.md`. Feature numbers are identifiers, not a required serial implementation order.

Repository: https://github.com/amitkohad/temporal-visual-designer. This repository is the development home for Nexus Flow Designer. Temporal provides the external durable execution engine.

## Experience design

Start UI work with the [experience specification](ux-design.md) and [Figma handoff register](design-handoff.md). [Open the editable Figma concepts](https://www.figma.com/design/6WxO7fCgLm0yq9UUv0o4wv). The revised spatial Designer canvas direction is user-approved as of 2026-10-23. Other screens, interaction states and implementation readiness remain subject to review and validation.
