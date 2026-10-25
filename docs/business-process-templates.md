# Business-process starter templates

The authenticated API provides two starting points for editable projects. Template creation saves a Dev draft; publish and activate it separately before starting an execution. Enable managed tasks in the local demo before running either template. See the [local launcher guide](local-development.md#enable-managed-approvals).

| Template ID | Approval sequence | Required workflow input |
|---|---|---|
| `employee-onboarding` | manager, HR, IT | `employee_name`, `department`, `start_date` (nonempty strings) |
| `purchase-requisition` | manager, finance | `requester`, `item` (nonempty strings), `amount` (number at least 0.01), `currency` (three-character string) |

All paths below are relative to the signed organization/environment API base. `GET /project-templates` lists metadata, version, assignment slots and input schemas. `GET /project-templates/employee-onboarding` reads one template. Both require project-read permission. Templates contain no default users or groups.

Create a purchase draft using `POST /project-templates/purchase-requisition/projects` with this body, replacing the example principal with a user object ID that your host resolves and authorizes:

```json
{
  "project_id": "purchasing",
  "name": "Purchase approvals",
  "assignments": {
    "manager": {"user_ids": ["22222222-2222-4222-8222-222222222222"]},
    "finance": {"user_ids": ["22222222-2222-4222-8222-222222222222"]}
  }
}
```

The object ID shown is the synthetic local-demo participant, suitable only for that demo. For onboarding, provide `manager`, `hr` and `it` assignments instead. Each assignment accepts `user_ids`, `group_ids` and optional `rule` (`any_one`, the default, or `all_approvers`). At least one user or group is required per slot. Every slot must be supplied, and extra slots are rejected. Group resolution and independent participant permission remain host responsibilities.

Creation returns 201 with the normal project revision. Repeating the request for the same project ID returns 409; read the existing project after an ambiguous response. UAT and Prod reject authoring. No release, activation or task is created at this stage.

Publish source revision 1 through `/projects/purchasing/releases`, activate the returned release through `/projects/purchasing/activation`, then call `/projects/purchasing/executions` with workflow ID `purchase-requisition` and, for example:

```json
{
  "workflow_id": "purchase-requisition",
  "idempotency_key": "purchase-001",
  "input": {"requester": "Ada", "item": "Laptop", "amount": 1200, "currency": "USD"}
}
```

Each pending task has a form containing the pinned request context as read-only fields. Submit `{"expected_revision":2,"values":{"confirmed":true,"notes":"Reviewed"}}` to its `/form` route, using the actual revision from the preview, then approve using the returned revision. Approval before a successful form submission fails. Rejection may be submitted without confirmation and ends the workflow through the existing proof-runtime failure. Approved tasks advance to the next configured slot; the last approval completes the process.

These templates collect approvals in a fixed order. They perform no external provisioning or purchasing. Dates and currency strings have only the documented starter constraints, and amounts have no conversion or accounting semantics. Task notes are retained in the form store rather than mapped to workflow output. The projects can be edited and versioned normally; the built-in template catalog does not update saved drafts.
