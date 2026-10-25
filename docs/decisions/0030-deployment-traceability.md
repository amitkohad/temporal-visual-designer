# ADR 0030: Deployment history and optional evidence metadata

Status: accepted for the local activation traceability increment.

Extend the existing activation/rollback operation with optional `evidence`: a change-request number (at most 128 characters) and notes (at most 2,000 characters). Each field may be omitted or null; supplied strings must contain non-whitespace characters. Treat these as opaque traceability text. No external change-request lookup, approval workflow or mandatory production evidence is introduced.

Evidence is immutable operation metadata persisted with the activation request, successful operation receipt and project pointer in the existing transaction. Exact retries return the original receipt and actor. Reusing the operation ID with changed evidence conflicts. Adding notes later requires a distinct deployment operation; this increment exposes no historical editing endpoint. Rejected attempts remain in the existing audit store but do not appear in the successful-operation history.

Absent or null evidence is omitted by request serialization. This preserves pre-upgrade canonical fingerprints and allows old receipts to be retried without creating another deployment. Old serialized records remain readable. A supplied empty evidence object is allowed and is a distinct normalized request from absent evidence. Existing no-evidence API responses keep their previous representation.

Expose authenticated metadata-only history and operation lookup alongside current activation. History uses descending activation revisions, an exclusive positive revision cursor and pages of 1–100 rows (default 50). Select summary fields from stored operation JSON in SQLite rather than loading workflow content or release snapshots into the response. No table/schema migration is required. Empty history is returned when no successful operation exists in the requested scope; individual missing operations return 404.

History and operation reads require fresh scoped `run.read`, matching existing current-activation reads. Activation and rollback retain their existing permissions, including production-support policy. The audit event continues to identify the operation, actor, checksum and outcome; optional evidence is retrieved through the immutable operation receipt. Optional text does not grant authority, change release checksums or enter workflow execution plans.

Approval-file uploads, attachment scanning/storage, browser Admin screens, package transfer, production database providers and live Entra verification remain separate work. This increment supplies optional text evidence and successful deployment history, not the complete F09 evidence or promotion feature.
