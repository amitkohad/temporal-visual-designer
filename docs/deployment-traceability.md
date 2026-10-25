# Deployment traceability

Activation and rollback can record optional change-request metadata. Approval continues to happen outside Nexus Flow Designer. The server retains the existing environment permissions; a change-request number confers no authority and missing evidence never blocks an otherwise authorized deployment.

Send the ordinary signed `POST /projects/{project_id}/activation` request, optionally adding evidence:

```json
{
  "project_id": "purchasing",
  "release_id": "release-identifier",
  "operation_id": "deploy-2026-001",
  "expected_revision": 0,
  "configuration_revision": "local-v1",
  "action": "activate",
  "evidence": {
    "change_request_number": "CHG-1042",
    "notes": "Approval retained in the organization's change system"
  }
}
```

Use the actual published release ID, current activation revision and configured destination revision. Set `action` to `rollback` to select a retained compatible release. Deployment remains project-wide and running workflows retain their pinned versions.

Both evidence fields are optional, may be null and accept plain text. Supplied strings must contain non-whitespace characters. The change-request number allows up to 128 characters and notes up to 2,000. The server does not validate change-system references or interpret approval text. File attachments are not supported yet.

Retain the exact request when retrying after an unavailable response. The operation ID identifies the complete normalized request, including supplied evidence. Exact retries return the original actor and receipt without another activation; changed evidence with the same operation ID conflicts. Omitted or null evidence retains pre-upgrade request identity. An explicit empty evidence object is accepted but represents a different request from omitted evidence. Historical metadata is immutable; there is no note-edit endpoint.

`GET /projects/{project_id}/activations?limit=10` returns recent successful operations in descending activation-revision order. Use the returned `next_before_revision` as `before_revision` for the next page. `GET /projects/{project_id}/activations/deploy-2026-001` reads the corresponding historical operation, including optional evidence. Reads require fresh `run.read` in the organization/environment scope; Prod support retains its existing access policy. Metadata remains available after a later deployment changes the active version.

History contains checksums and deployment metadata, excluding project snapshots and workflow payloads. Failed attempts remain in the existing internal audit store. Empty scoped history is returned when there are no successful operations; individual missing operations return 404. Current activation keeps its existing nullable representation when no release is active.
