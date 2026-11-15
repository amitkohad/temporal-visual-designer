# Schedule starts increment

Implemented on `feature/schedule-starts-1b62`. Scope: timezone-aware schedule create/list/disable and due-occurrence fire into the existing durable start path. See [ADR 0069](../../docs/decisions/0069-schedule-starts.md).

## Implemented boundaries

- `nexusflow/modules/durable_runtime/schedule_types.py`: frozen schedule definition, occurrence key helper, IANA timezone validation and typed `ScheduleError`.
- `nexusflow/modules/durable_runtime/schedules.py`: five-field cron evaluation, SQLite schedule/occurrence store, create/list/disable, sync/async due-fire with `allow`/`skip`, restart-safe cursor and occurrence records.
- `nexusflow/modules/durable_runtime/starts.py`: accepts `source="schedule"` alongside manual/api.
- `nexusflow/apps/local_schedules.py` + `nexusflow-local enable-schedules`: Dev-only profile marker and `schedules.db`; `serve` runs an owned poll loop when enabled.
- Signed HTTP: `POST/GET .../projects/{id}/schedules` and `POST .../schedules/{id}/disable` (requires enabled local schedule provider).

RT-T01i and RT-T03n are complete for this schedule subset. Parents RT-T01b / RT-T03b remain open. This contributes to RT-FR-05 and RT-A6 without claiming full runtime acceptance.

## Remaining acceptance

Webhook/form starts, `queue` overlap, Temporal-running lifetime skip, checkpoints, projections, full interpreter (RT-T02), Entra and production providers stay open. No Designer palette UI in this increment.

## Verification

Verified locally on Python 3.12:

- `uv run pytest nexusflow/tests/runtime/test_schedules.py nexusflow/tests/local/test_schedule_profile.py nexusflow/tests/runtime/test_starts.py -q`: 25 passed.
- Ruff lint/format and strict mypy passed for the touched durable_runtime / local schedule modules.

No hosted CI, Entra or production-provider acceptance is claimed.
