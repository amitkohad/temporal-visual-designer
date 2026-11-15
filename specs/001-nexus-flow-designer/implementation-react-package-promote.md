# Implementation evidence: React package promote Dev→UAT UI (Phase 3.6)

## Scope delivered

- **F09-T03 (partial):** React Promote surface for export → validate → stage → materialize → activate against existing signed package/activation APIs. Critical-path client and component tests under `web/`. Thin App **Promote** nav mount.
- **F09-T05b (partial UI only):** Does **not** close secure package transfer, production configuration/capability providers, or cross-environment acceptance. Parents F09-T03 / F09-T05b remain open.

## Paths

| Path | Role |
|---|---|
| `web/src/package-api.ts` | Signed client: ZIP export, validate, stage, list/get import, materialize |
| `web/src/package-api.test.ts` | Critical-path request/parse/privacy tests |
| `web/src/Promote.tsx` | Promote surface + `mountPromote` export |
| `web/src/promote.css` | Promote layout styles |
| `web/tests/Promote.test.tsx` | Mocked Dev→UAT export/stage/materialize/activate path |
| `web/src/App.tsx`, `web/src/app-session.ts` | Thin `promote` nav mount |

## APIs used (no new backend)

- `GET /projects/{id}/releases/{release_id}/package`
- `POST /packages/validation` (raw `application/zip`)
- `PUT /package-imports/{operation_id}`
- `GET /package-imports`, `GET /package-imports/{operation_id}`
- `POST /package-imports/{import_operation_id}/materializations`
- `GET` / `POST /projects/{id}/activation` (via existing `release-api`)

## Verification

```sh
cd web && npm test -- --run src/package-api.test.ts tests/Promote.test.tsx tests/App.test.tsx
cd web && npm run build
```

Recorded locally on this change: related vitest tests passed; production build succeeded.

## Non-claims

- Fake/mocked HTTP and local-demo Hosted Dev ≠ Entra or multi-tenant production acceptance.
- local-demo default grants ≠ UAT `business_author` (Dev-only by default).
- Staging ≠ activation; materialization ≠ guaranteed later activation readiness.
- Historical `nexusflow/modules/09-package-promotion/ui/` path, Prod support evidence UI, and full F09-A01–A06 browser acceptance remain open.
