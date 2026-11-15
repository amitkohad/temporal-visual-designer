# Implementation evidence: React connection pickers + Dev HTTP mocks (Phase 3.3)

## Scope delivered

- **F08-T03 (partial):** Designer Connections surface, connection picker, and
  signed clients for existing HTTP connection + Dev mock-fixture admin APIs
  under `web/src/`.
- **Local host glue:** `nexusflow-local` now opens `connections.db` so Dev
  connection revision PUT/GET/history work once `connection.manage` is granted
  (`enable-mock-fixtures`). Mock fixture APIs remain opt-in via the existing
  enable command.

## Paths

| Path | Role |
|---|---|
| `web/src/connection-api.ts` | Signed connection + mock-fixture client, origin/fixture validation, pick-option helpers |
| `web/src/connection-api.test.ts` | Critical-path client + validation |
| `web/src/ConnectionPicker.tsx` | Reusable connection ID/revision picker |
| `web/src/Connections.tsx` | Dev Connections surface: register HTTP revisions, author/list mock fixtures |
| `web/src/connections.css` | Surface styles |
| `web/tests/Connections.test.tsx` | Mocked HTTP author → save connection + mock critical path |
| `web/src/App.tsx`, `web/src/app-session.ts` | Minimal Connections nav mount |
| `nexusflow/apps/local_bootstrap.py` | Wire `SQLiteConnectionRepository` for local-demo |
| `nexusflow/tests/local/test_mock_bootstrap.py` | Assert connection PUT after mock-fixture enablement |

## APIs reused (no new routes)

- `PUT/GET /connections/{id}/revisions/{revision}`
- `GET /connections/{id}/revisions`
- `PUT/GET /connections/{id}/revisions/{revision}/mock-fixture`
- `GET /mock-fixtures`

## Verification

```sh
cd web && npm test -- --run src/connection-api.test.ts tests/Connections.test.tsx tests/App.test.tsx
cd web && npm run build
uv run pytest -q nexusflow/tests/local/test_mock_bootstrap.py
```

Recorded locally on this change: 16 related vitest tests passed; production
build succeeded; 8 local mock-bootstrap tests passed.

## Non-claims

- Dev HTTP mocks and local-demo tokens ≠ live SMTP, public egress, Entra, or
  production-provider acceptance.
- Connection/mock administration ≠ workflow activity palette wiring, published
  HTTP JSON activity start, or registered mock worker dispatch (Phase 3.4+).
- Full F08-A01–A05 browser/UI acceptance remains open.
