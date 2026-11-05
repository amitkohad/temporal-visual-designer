# Run an explicit worker over persisted mock fixtures

Prepare synthetic fixtures using the existing signed administration API or trusted
host store. An explicit host can then register the persisted mock activity:

```python
from temporalio.worker import Worker

from nexusflow.apps.persistent_http_mocks import open_persistent_http_mock_activities
from nexusflow.modules.durable_runtime.workflow import ProofWorkflow

with open_persistent_http_mock_activities(
    database,
    worker_authorization,
    scope,
    namespace,
    task_queue,
    dev_scopes=(scope,),
) as activities:
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[ProofWorkflow],
        activities=[activities.dispatch],
    ):
        await run_host_until_stopped()
```

The surrounding host provides the client and the trusted configuration shown
above. It must supply an actual fresh worker authorization port; fixture admin
permission does not substitute for `worker.execute`. Stop/drain the worker before
exiting the context, which closes its store. No worker or grant is created by the
factory. Normal application hosts are not changed to use it automatically.

The database must already exist and have the exact supported schema. The worker
opens it read-only, rejects writes and cannot initialize or repair fixture tables.
SQLite locking sidecars may be managed. Existing administrative writers may add
new immutable revisions, visible to subsequent worker reads. Use the exact digest
pin returned by verified fixture metadata; every runtime lookup validates it.

Only mock HTTP JSON dispatch is configured. Live inputs fail without transport
fallback. Activity results contain operation ID, status and mode only. Use
synthetic inputs too: proof input literals may exist in Temporal history.

This host factory and direct compiled-plan acceptance do not make HTTP activity
metadata publishable as executable project artifacts. Default launcher worker
registration, app publication/activation/start, browser UI, fixture packages and
production/cloud authorization remain separate work. See
[ADR 0060](decisions/0060-persistent-mock-worker.md).
