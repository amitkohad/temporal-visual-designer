# Form starts increment

Implemented on `feature/webhook-form-starts-4245`. Scope: signed form-start register/disable and schema-validated submit into the existing durable start path. See [ADR 0071](../../docs/decisions/0071-form-starts.md).

## Implemented boundaries

- `nexusflow/modules/durable_runtime/form_start_types.py`: frozen form-start definition/submission records and typed `FormStartError`.
- `nexusflow/modules/durable_runtime/form_starts.py`: SQLite form-start/submission store, pinned `FormDefinition` validation, create/list/disable, submit into starts with `source=form`.
- `nexusflow/modules/durable_runtime/starts.py`: accepts `source="form"` alongside manual/api/schedule/webhook.
- `nexusflow/apps/local_form_starts.py` + `nexusflow-local enable-form-starts`: Dev-only profile marker and `form-starts.db`.
- Signed HTTP: `POST/GET .../projects/{id}/form-starts`, `POST .../form-starts/{id}/disable`, `POST .../form-starts/{id}/submit`.

RT-T01k and RT-T03p are complete for this form-start subset. Parents RT-T01b / RT-T03b remain open. This contributes to RT-FR-05 without claiming full runtime acceptance.

## Remaining acceptance

Webhook body HMAC, attachments on start forms, Designer palette UI (Phase 3.10), checkpoints, projections, full interpreter (RT-T02), Entra and production providers stay open.

## Verification

Verified locally on Python 3.12:

- `uv run pytest nexusflow/tests/runtime/test_form_starts.py nexusflow/tests/local/test_form_start_profile.py nexusflow/tests/runtime/test_webhooks.py nexusflow/tests/local/test_webhook_profile.py nexusflow/tests/runtime/test_starts.py nexusflow/tests/runtime/test_schedules.py nexusflow/tests/local/test_schedule_profile.py -q`: 36 passed.
- Ruff lint/format passed for the touched durable_runtime / local form-start modules.

No hosted CI, Entra or production-provider acceptance is claimed.
