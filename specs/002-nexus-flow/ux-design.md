# Product experience specification

Status: Designer spatial-canvas visual direction approved on 2026-10-23; complete interaction designs and implementation remain pending. Product: Nexus Flow Designer.

## Figma source and design workflow

Design file: https://www.figma.com/design/6WxO7fCgLm0yq9UUv0o4wv — Nexus Flow Designer — Product Experience.

Figma MCP authentication verified on 2026-10-23. The file is an initial design workspace. Record actual frame/node IDs and review evidence in design-handoff.md; do not treat file creation or static screens as a completed interactive prototype.

Use Figma MCP to inspect available libraries, assemble editable screens, read back design context and screenshots, and iterate before implementing UI. Prefer reusable library components and semantic tokens. No application source, local web prototype or screenshot-to-code implementation is authorized by this design phase. Figma Plugin API operations create design artifacts only.

## Experience principles

Make the next useful action obvious. Use business language, examples, progressive disclosure and consistent navigation. Keep advanced runtime/retry/expression controls available behind labeled sections. Favor a calm canvas, strong typography, restrained accent color and clear status labels over decorative effects. Never rely on color, hover or drag alone. AI workflow generation is not implied by “next generation”; it needs a separate requirement.

## Requirements

- UX-001: Persistent organization, environment and project context; Dev/UAT/Prod shown with text plus visual distinction. Show saved/unsaved/saving/conflict state and active draft/release identity.
- UX-002: Start from an onboarding/purchase template or guided blank state. Explain missing setup and offer a direct action. Search activities by business name, category and purpose, with examples and connection availability.
- UX-003: Add steps through contextual insert controls, keyboard search or drag/drop. Provide undo/redo, duplicate, labeled branches, auto-arrange, fit-to-view, minimap and a synchronized outline for non-pointer navigation. Undo affects authoring only, never external execution.
- UX-004: Selection reveals a focused properties panel with input/output tabs, typed variable picker, sample values and plain-language approval-rule summaries. Reveal advanced policies progressively. Preserve unsaved edits when selection changes or surface an explicit recovery choice.
- UX-005: Validation issues link to the precise step/field and explain how to fix it. Design empty, loading, unavailable connector, unauthorized, stale revision, offline/save failure and destructive-action states; preserve work after recoverable failure.
- UX-006: Build forms with the same components, labels and validation behavior used in the task portal. Preview desktop/mobile forms and conditional fields. An accessible alternative to drag/drop is mandatory.
- UX-007: Debug in place with an explicit Dev test banner, mock/real connection mode, selected invocation, breakpoint, step-over/continue/cancel and redacted input/output inspector. Running, paused, waiting, failed and disconnected states must be distinguishable without color.
- UX-008: Admin presents import → validation → bindings → activation with release comparison, clear environment and role context. Optional production evidence must be labeled optional and never block an otherwise valid activation. Explain rollback affects new runs only.
- UX-009: Task portal prioritizes pending/due work, shows request context and approval progress, supports accessible decisions, comments, attachments, delegation and reassignment. Mobile task completion is first-release scope; full mobile canvas editing is not.
- UX-010: Design toward WCAG 2.2 AA: visible unobscured focus, full keyboard paths, non-drag alternatives, contrast at least 4.5:1 for ordinary text and 3:1 for large text/essential controls, meaningful labels, non-color states and reduced motion. Verify implementation with assistive technology later; Figma annotations alone do not establish compliance.
- UX-011: Desktop reference frames at 1440 and 1280 pixels; collapsed-panel behavior for constrained authoring layouts; task/form frames at 390 pixels. Preserve usability at 200% zoom and responsive reflow outside the spatial canvas, with outline alternative for canvas navigation.
- UX-012: Design system covers semantic colors, spacing, typography, radii, focus, motion, buttons, inputs, activity nodes, branches, inspectors, tables, task cards and status badges with default/focus/disabled/error/loading states. No credentials or real employee data in mockups.

## Screen inventory

| ID | Screen and key states | Feature owner |
|---|---|---|
| UX-S01 | Projects/templates, empty project, revision history/conflict | 02 + 03 |
| UX-S02 | Designer canvas, add-step search, properties, mapping, validation | 03 |
| UX-S03 | Form builder and desktop/mobile preview | 03 |
| UX-S04 | Dev debugger paused/running/waiting/error/reconnect | 07 |
| UX-S05 | Task inbox/detail, approval rules/progress, delegation, attachment error | 06 |
| UX-S06 | Admin activity import, validation, publication, palette availability | 04 |
| UX-S07 | Admin project import/bind/activate/rollback, optional Prod evidence | 09 |
| UX-S08 | Connection setup/test, denied/unavailable/secret redaction | 08 |

## Independently testable design acceptance

- UX-A01: Given a blank project prototype, a representative business user can find a template, insert an approval, select an Entra group and explain its approval rule without coding terminology.
- UX-A02: Given a wrong mapping or save conflict, the prototype shows a located, actionable recovery path and preserves the user's intended edits.
- UX-A03: Given keyboard-only navigation, the annotated prototype offers complete add/configure/connect/validate paths without mandatory dragging. Later browser tests verify actual focus and screen-reader behavior.
- UX-A04: Given a paused test, users can identify Dev, connection mode, selected invocation, variables and next-step effect without mistaking inspection for production execution.
- UX-A05: Given a Prod activation with no evidence, an authorized support user can proceed; a business-author view communicates lack of permission. Rollback scope is explicit.
- UX-A06: Given a 390-pixel task/form prototype, approval, validation, attachment status and delegation remain readable and operable without horizontal form scrolling.
- UX-A07: Given the frozen component library and frame references, independently assigned UI agents can identify exact shared components/states and implement their own feature without editing another agent's files.

## Usability review gate

Recruit five representative business users for authoring/task scenarios and production support for deployment scenarios. Proposed acceptance target: at least four of five business users complete the core create/configure/fix-error/debug tasks without facilitator intervention, and no participant mistakes Dev for Prod or executes an unintended destructive action. Record completion, errors, assistance and comments; failure triggers revision/retest. Timing targets follow a baseline rather than invented performance promises. These are targets, not completed research.

## Design delivery and agent ownership

UX-T001: Freeze journeys, terminology, screen/state inventory and library/tokens; one design-system owner edits shared Figma foundations and the handoff index.

UX-T002 [P]: Design Designer/forms frames UX-S01–03; one owner and dedicated frames.

UX-T003 [P]: Design debugger/task frames UX-S04–05; separate frames, consume frozen components.

UX-T004 [P]: Design Admin/connection frames UX-S06–08; separate frames, consume frozen components.

UX-T002–004 depend on UX-T001, not on one another. Never edit shared main components concurrently. Different agents use explicit frame IDs/owned pages and the same approved token revision.

UX-T005: Integrate navigation and clickable prototypes, inspect screenshots/context, verify coverage and conduct usability review. Depends on UX-T002–004. Record defects and update designs.

UX-T006: Freeze approved frame/component references and interaction annotations in design-handoff.md. Depends on UX-T005 and review of the relevant screen scope. Canvas visual-direction approval is already recorded; do not treat it as proof that remaining states or tests are complete. UI implementation tasks require this gate; backend/contract work may proceed independently after their own gates when implementation is authorized. Subsequent design changes require affected feature review, not silent token drift.

## Reference-led spatial canvas revision

The user supplied a workflow-designer reference on 2026-10-23 and requested a similar canvas. The latest direction supersedes the initial stacked-card editor concept. Use [spatial concept frame 5:14](https://www.figma.com/design/6WxO7fCgLm0yq9UUv0o4wv?node-id=5-14) as the user-approved visual direction (2026-10-23); interaction completeness and implementation readiness remain subject to the documented gates.

UX-013: Make the spatial graph the dominant workspace: subtle dotted grid; compact activity icons with labels beneath; circular start/end events; diamond decisions; directional orthogonal or gently curved connectors; named ports and explicit branch labels. Show both alternative paths and their convergence. Conditional convergence must not be confused with an all-branches parallel join.

UX-014: Provide narrow collapsible activity and execution rails, searchable expanded activity labels, contextual properties, fit/zoom controls and minimap. Keep generous diagram spacing and connector-label clearance. Icons require tooltips, accessible names and keyboard alternatives; production icons must be consistent vectors rather than platform-dependent emoji glyphs.

UX-A08: Given the reference purchase process, a user can follow start → approval → decision, identify Approved versus Rejected, follow the subworkflow or status-update path to the end, and inspect the selected decision without expanding a full activity list. Both branches remain distinguishable without color. During layout/reflow, connectors never obscure activity labels or change graph semantics.

The current Figma frame is an editable static concept. Interactive connection handles, snapping, tooltip/focus variants, collapsed-inspector behavior and usability testing remain required before implementation. Graph/form semantic contracts remain unchanged by visual styling.