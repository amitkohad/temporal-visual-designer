# Implementation evidence: React Admin catalog publish lifecycle (Phase 3.7)

## Scope delivered

- **F04-T004 (partial):** Admin import / publish / search / deprecate / withdraw UI and critical-path client/component tests under `web/src/`. Uses existing signed activity-admin APIs (F04-T006c). Does not implement execution UI.
- Parent F04-T004 remains open for full A01–A06 acceptance and any remaining module-path UI expectations beyond this Dev-loop React slice.

## Paths

| Path | Role |
|---|---|
| `web/src/catalog-api.ts` | Signed client: ZIP intake, publish, search, resolve, transitions |
| `web/src/catalog-api.test.ts` | Critical-path request/parse/privacy tests |
| `web/src/AdminCatalog.tsx` | Admin surface + `mountAdminCatalog` export |
| `web/src/admin-catalog.css` | Admin layout styles |
| `web/tests/AdminCatalog.test.tsx` | Mocked import → publish → deprecate path |
| `web/src/App.tsx`, `web/src/app-session.ts` | Thin `admin` nav mount |

## APIs used (no new backend)

- `POST /activity-imports` (raw `application/zip`)
- `GET /activity-imports/{digest}` (client supports; UI uses post-import response)
- `POST /activities/publish`
- `GET /activities`
- `POST /activities/resolve` (client supports)
- `POST /activities/transitions`

## Verification

```sh
cd web && npm test -- --run src/catalog-api.test.ts tests/AdminCatalog.test.tsx tests/App.test.tsx
cd web && npm run build
```

Recorded locally on this change: related vitest tests passed; full `web` suite 215 passed; production build succeeded.

## Non-claims

- Fake/mocked HTTP and local-demo Hosted Dev ≠ Entra or production-provider acceptance.
- local-demo default grants ≠ `activity_publisher`; capability fixtures ≠ deployed custom Python workers (F04-T007 / F04-A08).
- Archive download UI, Designer palette refresh for custom activities, and full F04-A01–A06 browser acceptance remain open.
