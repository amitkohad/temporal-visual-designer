# Live email SMTP send (opt-in local-demo)

Dev-usable SMTP send path with secrets. Offline preflight and MIME remain
non-sending. See [ADR 0067](decisions/0067-email-smtp-secret-resolution.md) and
[ADR 0068](decisions/0068-temporal-live-email.md).

## How to try

```sh
uv sync --locked
uv run nexusflow-local init
uv run nexusflow-local enable-mock-fixtures   # connection.manage for admin APIs
uv run nexusflow-local enable-live-email
uv run nexusflow-local put-smtp-secret \
  --reference hr-mail-credentials-v1 \
  --username "$SMTP_USER" \
  --password "$SMTP_PASS"
# Save an email connection revision with secret_reference=hr-mail-credentials-v1
# (Connections UI → Register email SMTP connection, or signed PUT).
uv run nexusflow-local serve --ui
```

Critical-path checks:

```sh
uv run pytest -q nexusflow/tests/connectors/test_smtp_secret_types.py \
  nexusflow/tests/connectors/test_authorized_email_execution.py \
  nexusflow/tests/connectors/test_temporal_email_policy.py \
  nexusflow/tests/local/test_live_email_profile.py
uv run pytest -q -m integration \
  nexusflow/tests/runtime/test_email_live_dispatch_integration.py
```

## Non-claims

- `email-secrets.json` ≠ Azure Key Vault / production secret provider
- Fake Temporal executor / local AUTH ≠ public SMTP egress acceptance
- Opt-in worker registration ≠ default-on launcher or catalog publication
- Connections email metadata ≠ Designer email activity palette
- Parent F08-T01–T06 and F04-T007 remain open
