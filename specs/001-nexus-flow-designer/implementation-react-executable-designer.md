# React executable Designer subset (Phase 1A)

Branch `feature/mvp-executable-designer-5c79` based on main after MVP Dev-loop
contracts merge (#67). Mocked HTTP / Vitest / Playwright only; no Entra,
Temporal, or production-provider claim.

## Delivered

- `template-api.ts`: list/get MVP templates, create Dev draft, validate project
  content; `publishBlockedByValidation` for 1C publish gating.
- `workflow-edit.ts`: insert/delete/reorder helpers; wire rebuild for approval/wait.
- `project-projection.ts`: `withExecutableWorkflow` / `projectContentForSave`
  (layout-only path unchanged when topology is stable).
- `Projects.tsx`: template create form, insert/delete/reorder controls, Validate,
  executable save via `PUT` + `expected_revision`, validation issue list,
  `releaseControls` slot and `onPublishGateChange` for App/1C composition.

## Verification (this change)

- Vitest: `template-api`, `workflow-edit`, `project-projection`, `Projects` —
  including template→insert wait→executable save and validation publish gate.
- Playwright `e2e/projects.spec.ts`: layout save retained; conflict reload;
  template create → insert wait → executable save.

## Remaining (not claimed)

- App.tsx mount of publish gate / ReleaseControls (coordinator / 1C).
- Live signed ASGI/SQLite/Temporal template→publish→activate→approve loop.
- Form builder, custom activities, expressions, descriptor-backed template catalog.
