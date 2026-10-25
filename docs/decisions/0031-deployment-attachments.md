# ADR 0031: Optional deployment evidence attachments

Status: accepted for the local attachment increment.

Allow optional files to be attached to an existing successful activation or rollback operation. Upload is a separate transaction after deployment. Missing or failed uploads never block activation, and attaching a file does not alter an operation request, its text evidence, release checksum, active pointer or execution plan. Historical operations remain immutable.

Accept raw `application/octet-stream` bytes through a PUT addressed by a caller-selected attachment ID. Require fresh scoped `package.activate` for uploads and fresh `run.read` for listing/downloads. Retain the existing role policy, including Prod support authorization. Successful operation existence is checked in the same scope; missing operations return 404. Attachment metadata records the verified uploader and upload time, separately from the original deployment actor and timestamp.

Limit files to 2 MiB and each operation to 10 attachments. Reject empty bodies. Use an additive SQLite table with a composite scope/project/operation/attachment key and a foreign key to the existing immutable successful-operation table. Blob and metadata commit atomically. An immediate transaction serializes quota checks across connections. Check an existing ID before the quota: identical bytes and display label return the original metadata, even at capacity; changed bytes or label conflict. Retry the same ID after an unavailable response. There is no edit/delete endpoint.

Display names are opaque, nonblank printable labels of at most 128 characters. They are never filesystem paths or response filenames. Bytes are not extracted, interpreted, rendered, installed or executed. Metadata listing selects no blobs. Download verifies the stored size/checksum and returns a fixed `deployment-evidence.bin` attachment with `application/octet-stream`, `X-Content-Type-Options: nosniff`, no-store and correlation headers. This is a bounded complete download, without ranges or streaming.

The existing global HTTP request limit remains 6,000,000 bytes, checked while buffering before route authorization; the smaller 2 MiB attachment limit applies after scoped authorization and media validation. Oversize uploads return 413, a full operation quota or conflicting ID returns 409, invalid input returns 422, and unavailable/corrupt storage fails safely with 503. Upload validation and persistence run outside the event loop.

Opaque storage is not approval verification, signature validation or malware scanning. Those policies and production blob-store/database migrations remain separate deployment work. This increment does not change the package format, implement a change-request workflow, grant roles, or deliver Admin attachment screens.
