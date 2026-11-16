# Webhook starts increment

Implemented on `feature/webhook-form-starts-4245`. Scope: authenticated webhook register/disable and secret+replay deliver into the existing durable start path. See [ADR 0070](../../docs/decisions/0070-webhook-starts.md).

## Implemented boundaries

- `nexusflow/modules/durable_runtime/webhook_types.py`: frozen webhook definition, secret digest helper, delivery timestamp parse and typed `WebhookError`.
- `nexusflow/modules/durable_runtime/webhooks.py`: SQLite webhook/delivery store, create/list/disable, secret-authenticated deliver with replay window into starts with `source=webhook`.
- `nexusflow/modules/durable_runtime/starts.py`: accepts `source="webhook"` alongside manual/api/schedule/form.
- `nexusflow/apps/local_webhooks.py` + `nexusflow-local enable-webhooks`: Dev-only profile marker and `webhooks.db`.
- Signed HTTP admin: `POST/GET .../projects/{id}/webhooks` and `POST .../webhooks/{id}/disable`.
- Secret-authenticated ingress: `POST .../webhooks/{id}/deliver` with `X-Nexusflow-Webhook-Secret`, `X-Nexusflow-Timestamp`, `X-Nexusflow-Delivery-Id`.

RT-T01j and RT-T03o are complete for this webhook subset. Parents RT-T01b / RT-T03b remain open. This contributes to RT-FR-05 and RT-A2/A6 without claiming full runtime acceptance.

## Remaining acceptance

Form starts (separate evidence), checkpoints, projections, full interpreter (RT-T02), Entra, HMAC body signatures and production providers stay open. No Designer palette UI in this increment.

## Verification

Verified locally on Python 3.12:

- `uv run pytest nexusflow/tests/runtime/test_webhooks.py nexusflow/tests/local/test_webhook_profile.py nexusflow/tests/runtime/test_starts.py -q`: included in the combined 36-pass suite with form/schedule starts.
- Ruff lint/format passed for the touched durable_runtime / local webhook modules.

No hosted CI, Entra or production-provider acceptance is claimed.
