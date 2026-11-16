# Implementation evidence: React Prod support + evidence/attachments UI (Phase 3.10)

## Scope delivered

- **F09-T03 (partial):** React Prod support surface for current activation, successful deployment history, optional evidence text on activate, and bounded attachment upload/list/download against existing signed APIs. Critical-path client and component tests under `web/`. Thin App **Prod** nav mount.
- **F09-T05b (partial UI only):** Does **not** close secure package transfer, production configuration/capability/blob/scanning providers, or cross-environment acceptance. Parents F09-T03 / F09-T05b remain open.

## Paths

| Path | Role |
|---|---|
| `web/src/deployment-api.ts` | Signed client: activation history/detail, attachment upload/list/download |
| `web/src/deployment-api.test.ts` | Critical-path request/parse/privacy tests |
| `web/src/ProdSupport.tsx` | Prod support surface + `mountProdSupport` export |
| `web/src/prod-support.css` | Prod support layout styles |
| `web/tests/ProdSupport.test.tsx` | Mocked history / evidence activate / attachment path + Prod grant denial |
| `web/src/release-api.ts` | Additive optional `evidence` on `activate` + parse |
| `web/src/App.tsx`, `web/src/app-session.ts` | Thin `prod` nav mount |

## APIs used (no new backend)

- `GET /projects/{id}/activation` (current)
- `POST /projects/{id}/activation` (optional `evidence`)
- `GET /projects/{id}/activations`
- `GET /projects/{id}/activations/{operation_id}`
- `PUT /projects/{id}/activations/{operation_id}/attachments/{attachment_id}`
- `GET /projects/{id}/activations/{operation_id}/attachments`
- `GET /projects/{id}/activations/{operation_id}/attachments/{attachment_id}`

## Local Prod grant (like UAT for Promote)

local-demo `init` grants `business_author` on **Dev only**. Prod activate/history/attachment routes require a server-owned `production_support` grant. Token roles alone do not authorize.

Example after `uv run nexusflow-local init` (stop `serve` first if running):

```python
from nexusflow.apps.local_bootstrap import TENANT_ID, USER_ID
from nexusflow.modules.identity_environments.repository import SQLiteIdentityRepository
from nexusflow.modules.identity_environments.types import Grant, Scope

repo = SQLiteIdentityRepository(".nexusflow-local/identity.db")
repo.save_grant(
    Grant(
        "local-prod-support",
        Scope(organization_id="local-demo", environment_id="prod"),
        TENANT_ID,
        "user",
        USER_ID,
        "production_support",
    ),
    expected_revision=0,
)
repo.close()
```

Without that grant, the Prod surface still connects the session and surfaces a clear forbidden message. Missing optional evidence text never blocks an otherwise authorized Prod activation; unauthorized identities (including Dev-only authors) are denied.

## How to try (`serve --ui`)

```sh
uv sync --locked
cd web && npm ci && npm run build && cd ..
uv run nexusflow-local init
# optional: add production_support grant as above
uv run nexusflow-local serve --ui
```

1. Open the printed `url`.
2. Optional: **Projects** → Connect so Prod can reuse the token.
3. **Prod** → Connect (environment `prod`) → review history / current activation.
4. Activate with optional change-request/notes when a Prod release exists; then upload/list/download attachments on the selected operation.

## Verification

```sh
cd web && npm test -- --run src/deployment-api.test.ts src/release-api.test.ts tests/ProdSupport.test.tsx tests/App.test.tsx
cd web && npm run build
```

Recorded locally on this change: related vitest tests passed; production build succeeded.

## Non-claims

- Fake/mocked HTTP and local-demo Hosted Dev ≠ Entra or multi-tenant production acceptance.
- local-demo default grants ≠ Prod `production_support`.
- Optional evidence/attachments ≠ approval authenticity or malware scanning.
- Historical `nexusflow/modules/09-package-promotion/ui/` path, production blob providers (F09-T01b/T02b), and full F09-A01–A06 browser acceptance remain open.
