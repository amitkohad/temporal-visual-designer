# Feature 03: Visual designer, forms, and validation

## Outcome and scope

Business users build employee onboarding and purchase requisition processes without writing code. Own project explorer presentation, canvas authoring, properties, typed variable selection, form authoring/rendering, and static validation. Feature 02 stores revisions, feature 04 supplies palette descriptors, feature 05 executes definitions, and feature 07 owns debugging. This specification adds no application code.

## Requirements

- **F03-FR01:** Support multiple workflows and forms within a project; edit stable resource/node IDs, labels, connections, and separate canvas layout.
- **F03-FR02:** Author sequence, decision/switch, parallel/join, bounded iteration, reusable workflow, wait, human-task, connector, and completion blocks through descriptors. Require explicit loop bounds and structured joins; reject unsupported cycles.
- **F03-FR03:** Configure blocks through schema-driven forms and map typed workflow inputs or prior step outputs with a variable picker. Detect invalid types, missing fields, unavailable outputs, and invalid expressions before publication.
- **F03-FR04:** Author forms with typed fields, required/range/format validation, conditional visibility, read-only rules, and attachment references. Enforce the same validation server-side; hidden fields cannot bypass required business rules or gain write permission.
- **F03-FR05:** Render accessible forms usable by keyboard with associated labels, focusable errors, and a configurable business-facing layout. Never treat expression text, field labels, or connector metadata as executable HTML or Python.
- **F03-FR06:** Report validation issues with resource/node/field locations; validate missing starts/ends, unreachable nodes, malformed branches, unresolved references, incompatible activity versions, and recursion/cycles in reusable workflow references.
- **F03-FR07:** Preserve last acknowledged revision on failed saves, expose unsaved state, reject stale revision writes, and support retry without silently overwriting changes.
- **F03-FR08:** Provide onboarding and purchase requisition authoring templates using published catalog descriptors. A template has no credentials or automatic external effects.
- **F03-FR09:** Submit form values only through authorized task/start APIs; render server-defined editable fields and redact secrets from previews.

## Data and contracts

Follow [shared contracts](../../contracts.md). WorkflowDefinition has stable IDs, versioned blocks, explicit edges, typed inputs/outputs, policies, and authoring layout. FormDefinition has stable field IDs, schema, layout, conditions, and allowed bindings. ValidationIssue identifies code, severity, resource, node/field path, and business-facing message. Expression scope is typed and side-effect-free, with explicit limits; no arbitrary language evaluation. Form conditions and workflow mappings share versioned expression semantics with the runtime. Project, palette, validation, task submission, and debugger navigation use replaceable ports.

## Acceptance scenarios

- **F03-A01 (positive):** Given a blank project and fake catalog, when a business user builds a request, amount decision, approval, and completion flow, then save/reload retains stable IDs, mappings, form fields, and layout.
- **F03-A02 (negative):** Given an output available only in another branch or an incompatible field type, when mapped into a step, then validation identifies the exact field and publication is blocked.
- **F03-A03 (negative):** Given an unbounded cycle, unmatched join, unreachable node, or recursive workflow reference, when validated, then each invalid structure receives a located error.
- **F03-A04 (positive):** Given a conditional required field, when the form is rendered and submitted using keyboard controls, then visibility, validation, focus, and submitted typed values follow the form definition.
- **F03-A05 (security):** Given tampered read-only values or malicious labels/expressions, when submitted/rendered, then server validation rejects unauthorized changes and content is displayed without executing code.
- **F03-A06 (recovery):** Given a save fails or conflicts, when the user retries or reloads, then acknowledged content remains intact and unsaved changes are explicitly recoverable rather than silently discarded.
- **F03-A07 (positive):** Given either reference template, when opened with fake task/connector descriptors, then it validates and contains no embedded secrets or external execution.

## Success criteria and exclusions

All scenarios run without Temporal, Entra, or connector credentials using fake ports. UI coverage verifies keyboard operation and a complete author/save/reload path. Server-side and UI validation share fixtures with runtime expression tests. Advanced collaborative editing, arbitrary graph cycles, code editing, execution, and debugging engine behavior are excluded.

## Experience acceptance extension

F03-FR10: Implement UX-001–006/010–012 from [experience specification](../../ux-design.md). Include guided template/blank states, business-language activity search, contextual and keyboard insert, undo/redo, auto-arrange, minimap/outline, focused configuration, accessible form preview and recoverable validation/save states. F03-A08: Given the reviewed Figma journey, a business user can author and repair the reference flow using keyboard or pointer, identify save status, and retain work after a save failure. Map prototype review to UX-A01–03/A07; verify actual browser behavior after implementation.

F03-FR11: Follow the reference-led spatial graph requirements UX-013/014. F03-A09: Verify UX-A08, labeled decision paths, correct conditional convergence, connector/label clearance, compact rails and selected-node properties. Visual layout never changes execution edges or join semantics.
