# Implementation evidence: React form builder + accessible renderer (Phase 3.1)

## Scope delivered

- **F03-T005 (partial):** Form builder UI, local preview, and shared accessible renderer under `web/src/`.
- **F03-T003b (partial):** Client rendering/layout for task + start preview modes; Tasks portal uses the shared renderer. Attachment semantics, server start-form adapters, and shared expression integration remain open.

## Paths

| Path | Role |
|---|---|
| `web/src/form-api.ts` | FormDefinition 0.1 parse/validate, draft mapping, project catalog upsert, inline `human_task.form` bind via existing project save |
| `web/src/form-api.test.ts` | Critical-path authoring helpers |
| `web/src/FormRenderer.tsx` | Accessible task/start/preview renderer (labels, describedby, aria-invalid, first-error focus, enum/select, write-only) |
| `web/src/FormBuilder.tsx` | Dev Forms surface: connect, catalog, edit fields, preview, save/bind |
| `web/src/forms.css` | Builder + renderer styles |
| `web/tests/FormRenderer.test.tsx` | Keyboard/label/error focus critical path |
| `web/tests/FormBuilder.test.tsx` | Mocked HTTP author → save/bind critical path |
| `web/src/Tasks.tsx` | Uses shared `FormRenderer` for managed task forms |
| `web/src/App.tsx`, `web/src/app-session.ts` | Minimal Forms nav mount |

## Persistence (no new backend routes)

- Project-level forms: `content.forms[]` via existing `PUT /projects/{id}`.
- Runtime binding: inline `nodes[].input_mapping.human_task.form.definition` (ADR 0025).
- Task submit/preview unchanged: `GET`/`POST …/tasks/{id}/form`.

## Verification

```sh
cd web && npm test -- --run src/form-api.test.ts tests/FormRenderer.test.tsx tests/FormBuilder.test.tsx tests/Tasks.test.tsx tests/App.test.tsx
cd web && npm run build
```

Recorded locally on this change: 21 related vitest tests passed; production build succeeded.

## Non-claims

- Fake/mocked HTTP and local-demo Hosted Dev ≠ Entra or production-provider acceptance.
- Start-form **mode** in the renderer is for shared UX/preview; dedicated start-form HTTP adapters remain open under F03-T003b.
- Attachments, general expressions, and full F03-A04/A05/A08 browser acceptance remain open.
