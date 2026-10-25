# Deployment approval files

Files are optional evidence attached to a successful deployment operation. Activate or roll back first, retain its `operation_id`, then upload any supporting file separately. Deployment is never blocked because evidence is absent. The existing [text evidence fields](deployment-traceability.md) remain available in the activation request.

All routes below use the signed organization/environment API base. Upload requires `package.activate`; list/download requires `run.read`. Existing production-support policy applies. Assigning a role in token text does not grant access.

Upload to `PUT /projects/{project_id}/activations/{operation_id}/attachments/{attachment_id}` with raw bytes and `Content-Type: application/octet-stream`. Use a stable attachment ID such as `approval-001`. Optional query parameter `display_name` defaults to `Approval evidence` and accepts a nonblank printable label up to 128 characters. It does not set the download filename or identify a filesystem path.

For example, after a successful operation, use these PowerShell variables with the actual base URL, project and bearer token from your configured host:

```powershell
$attachmentUrl = "$apiBase/projects/purchasing/activations/deploy-2026-001/attachments/approval-001?display_name=Approval%20letter"
Invoke-RestMethod -Method Put -Uri $attachmentUrl -Headers $headers `
  -ContentType 'application/octet-stream' -InFile './approval.pdf'
```

The file must contain 1–2,097,152 bytes. Each operation allows 10 files. Upload returns 201 metadata with the attachment ID, display label, SHA-256 checksum, size, uploader and time. Repeating the same ID, bytes and label returns the original metadata. Changed bytes or label with the same ID returns 409; use a new ID for a different file. After an unavailable response, retry the same ID and content. Exact retries still work when the operation already has 10 attachments.

`GET /projects/{project_id}/activations/{operation_id}/attachments` returns a bounded array of metadata. A successful operation with no files returns an empty array. A missing operation returns 404. `GET /projects/{project_id}/activations/{operation_id}/attachments/{attachment_id}` returns the original bytes after checksum verification.

Downloads are always private binary attachments named `deployment-evidence.bin`, with no-store and nosniff headers. Save or rename the downloaded file as appropriate; the service does not render or interpret it. Stored files do not establish approval authenticity or pass a malware scan. File edit/delete, inline preview and Admin screens are not available in this increment.

Attachments uploaded after a later deployment can still be read through their original operation. They do not modify deployment history, request retries, release checksums or running workflows. The uploader/time on the file records when it was attached, rather than changing the original deployment's actor/time.
