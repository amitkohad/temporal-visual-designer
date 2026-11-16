/**
 * Deployment history + evidence attachment client (F09 React Prod support slice).
 * Same privacy posture as project-api (bearer in Authorization only).
 */
import {
  ProjectApiError,
  isIdentifier,
  isObject,
  type ProjectConnection,
  type ProjectErrorCode,
} from "./project-api";

export type { ProjectConnection, ProjectErrorCode };
export { ProjectApiError as DeploymentApiError };

export interface DeploymentEvidence {
  change_request_number?: string | null;
  notes?: string | null;
}

export interface ActivationSummary {
  project_id: string;
  operation_id: string;
  release_id: string;
  revision: number;
  previous_release_id: string | null;
  action: "activate" | "rollback";
  runtime_version: string;
  configuration_revision: string;
  checksum: string;
  actor_id: string;
  created_at: string;
  evidence?: DeploymentEvidence | null;
}

export interface ActivationHistoryPage {
  project_id: string;
  items: ActivationSummary[];
  next_before_revision: number | null;
}

export interface AttachmentMetadata {
  attachment_id: string;
  display_name: string;
  checksum: string;
  size: number;
  actor_id: string;
  created_at: string;
}

export interface DownloadedAttachment {
  bytes: ArrayBuffer;
  filename: string;
  contentType: string;
}

export interface DeploymentClient {
  listActivations(
    projectId: string,
    limit?: number,
    beforeRevision?: number | null,
  ): Promise<ActivationHistoryPage>;
  getActivationOperation(
    projectId: string,
    operationId: string,
  ): Promise<ActivationSummary>;
  listAttachments(
    projectId: string,
    operationId: string,
  ): Promise<AttachmentMetadata[]>;
  uploadAttachment(
    projectId: string,
    operationId: string,
    attachmentId: string,
    content: ArrayBuffer | Uint8Array | Blob,
    displayName?: string,
  ): Promise<AttachmentMetadata>;
  downloadAttachment(
    projectId: string,
    operationId: string,
    attachmentId: string,
  ): Promise<DownloadedAttachment>;
}

const JSON_MAX = 6 * 1024 * 1024;
const ATTACHMENT_MAX = 2_097_152;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/;

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function nonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && DIGEST.test(value);
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 64 &&
    TIMESTAMP.test(value) &&
    Number.isFinite(Date.parse(value))
  );
}

function isActor(value: unknown): value is string {
  return (
    typeof value === "string" && value.length >= 1 && value.length <= 128
  );
}

function parseEvidence(value: unknown): DeploymentEvidence | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  if (!isObject(value)) throw new Error();
  const change = value.change_request_number;
  const notes = value.notes;
  if (
    !(
      change === undefined ||
      change === null ||
      (typeof change === "string" &&
        change.trim().length >= 1 &&
        change.length <= 128)
    )
  )
    throw new Error();
  if (
    !(
      notes === undefined ||
      notes === null ||
      (typeof notes === "string" &&
        notes.trim().length >= 1 &&
        notes.length <= 2000)
    )
  )
    throw new Error();
  const evidence: DeploymentEvidence = {};
  if ("change_request_number" in value)
    evidence.change_request_number = change as string | null | undefined;
  if ("notes" in value) evidence.notes = notes as string | null | undefined;
  return evidence;
}

function parseSummary(value: unknown): ActivationSummary {
  if (
    !isObject(value) ||
    !isIdentifier(value.project_id) ||
    !isIdentifier(value.operation_id) ||
    !isIdentifier(value.release_id) ||
    !positive(value.revision) ||
    !(
      value.previous_release_id === null ||
      isIdentifier(value.previous_release_id)
    ) ||
    (value.action !== "activate" && value.action !== "rollback") ||
    !isIdentifier(value.runtime_version) ||
    !isIdentifier(value.configuration_revision) ||
    !isDigest(value.checksum) ||
    !isActor(value.actor_id) ||
    !isTimestamp(value.created_at)
  )
    throw new Error();
  const evidence = parseEvidence(value.evidence);
  const summary: ActivationSummary = {
    project_id: value.project_id,
    operation_id: value.operation_id,
    release_id: value.release_id,
    revision: value.revision,
    previous_release_id: value.previous_release_id,
    action: value.action,
    runtime_version: value.runtime_version,
    configuration_revision: value.configuration_revision,
    checksum: value.checksum,
    actor_id: value.actor_id,
    created_at: value.created_at,
  };
  if (evidence !== undefined) summary.evidence = evidence;
  return summary;
}

function parseAttachment(value: unknown): AttachmentMetadata {
  if (
    !isObject(value) ||
    !isIdentifier(value.attachment_id) ||
    typeof value.display_name !== "string" ||
    value.display_name.trim().length < 1 ||
    value.display_name.length > 128 ||
    !isDigest(value.checksum) ||
    !positive(value.size) ||
    value.size > ATTACHMENT_MAX ||
    !isActor(value.actor_id) ||
    !isTimestamp(value.created_at)
  )
    throw new Error();
  return {
    attachment_id: value.attachment_id,
    display_name: value.display_name,
    checksum: value.checksum,
    size: value.size,
    actor_id: value.actor_id,
    created_at: value.created_at,
  };
}

async function boundedJson(
  response: Response,
  signal: AbortSignal,
): Promise<unknown> {
  const cancelBody = () => {
    void response.body?.cancel().catch(() => undefined);
  };
  const length = response.headers.get("content-length");
  if (
    length !== null &&
    (!/^\d+$/.test(length) || Number(length) > JSON_MAX)
  ) {
    cancelBody();
    throw new Error();
  }
  if (
    !(response.headers.get("content-type") ?? "")
      .toLowerCase()
      .split(";")[0]
      .trim()
      .endsWith("/json")
  ) {
    cancelBody();
    throw new Error();
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error();
  const chunks: Uint8Array[] = [];
  let count = 0;
  const abort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener("abort", abort, { once: true });
  try {
    if (signal.aborted) {
      abort();
      throw new Error();
    }
    for (;;) {
      const { done, value } = await reader.read();
      if (signal.aborted) throw new Error();
      if (done) break;
      count += value.byteLength;
      if (count > JSON_MAX) throw new Error();
      chunks.push(value);
    }
  } catch {
    void reader.cancel().catch(() => undefined);
    throw new Error();
  } finally {
    signal.removeEventListener("abort", abort);
    reader.releaseLock();
  }
  const bytes = new Uint8Array(count);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

async function boundedBytes(
  response: Response,
  signal: AbortSignal,
  maxBytes: number,
): Promise<ArrayBuffer> {
  const cancelBody = () => {
    void response.body?.cancel().catch(() => undefined);
  };
  const length = response.headers.get("content-length");
  if (
    length !== null &&
    (!/^\d+$/.test(length) || Number(length) > maxBytes)
  ) {
    cancelBody();
    throw new Error();
  }
  const media = (response.headers.get("content-type") ?? "")
    .toLowerCase()
    .split(";")[0]
    .trim();
  if (media !== "application/octet-stream") {
    cancelBody();
    throw new Error();
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error();
  const chunks: Uint8Array[] = [];
  let count = 0;
  const abort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener("abort", abort, { once: true });
  try {
    if (signal.aborted) {
      abort();
      throw new Error();
    }
    for (;;) {
      const { done, value } = await reader.read();
      if (signal.aborted) throw new Error();
      if (done) break;
      count += value.byteLength;
      if (count > maxBytes) throw new Error();
      chunks.push(value);
    }
  } catch {
    void reader.cancel().catch(() => undefined);
    throw new Error();
  } finally {
    signal.removeEventListener("abort", abort);
    reader.releaseLock();
  }
  if (count < 1) throw new Error();
  const bytes = new Uint8Array(count);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  );
}

async function attachmentBytes(
  content: ArrayBuffer | Uint8Array | Blob,
): Promise<ArrayBuffer> {
  if (content instanceof ArrayBuffer) {
    if (content.byteLength < 1 || content.byteLength > ATTACHMENT_MAX)
      throw new ProjectApiError("invalid_request");
    return content;
  }
  if (content instanceof Uint8Array) {
    if (content.byteLength < 1 || content.byteLength > ATTACHMENT_MAX)
      throw new ProjectApiError("invalid_request");
    return content.buffer.slice(
      content.byteOffset,
      content.byteOffset + content.byteLength,
    ) as ArrayBuffer;
  }
  if (typeof Blob !== "undefined" && content instanceof Blob) {
    if (content.size < 1 || content.size > ATTACHMENT_MAX)
      throw new ProjectApiError("invalid_request");
    return content.arrayBuffer();
  }
  throw new ProjectApiError("invalid_request");
}

/** Trigger a private browser download for opaque evidence bytes. */
export function downloadAttachmentBytes(
  downloaded: DownloadedAttachment,
  filename = downloaded.filename,
): void {
  const blob = new Blob([downloaded.bytes], {
    type: downloaded.contentType || "application/octet-stream",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export function createDeploymentClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): DeploymentClient {
  if (
    !connection ||
    !isIdentifier(connection.organizationId) ||
    !isIdentifier(connection.environmentId) ||
    typeof connection.token !== "string" ||
    connection.token.length < 1 ||
    connection.token.length > 16384 ||
    !/^[A-Za-z0-9._~+\/-]+=*$/.test(connection.token) ||
    typeof fetcher !== "function"
  )
    throw new ProjectApiError("invalid_request");
  const organizationId = connection.organizationId;
  const environmentId = connection.environmentId;
  const token = connection.token;
  const base = `/api/v1/organizations/${organizationId}/environments/${environmentId}`;

  async function requestJson(
    path: string,
    init: {
      method: "GET" | "PUT";
      body?: BodyInit;
      contentType?: string;
      ok?: ReadonlySet<number>;
    },
  ): Promise<unknown> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new ProjectApiError("unavailable"));
      }, 30000);
    });
    const ok = init.ok ?? new Set([200]);
    try {
      const operation = (async () => {
        const headers: Record<string, string> = {
          Authorization: `Bearer ${token}`,
          Accept: "application/json",
        };
        if (init.contentType) headers["Content-Type"] = init.contentType;
        const response = await fetcher(base + path, {
          method: init.method,
          headers,
          ...(init.body === undefined ? {} : { body: init.body }),
          signal: controller.signal,
          redirect: "error",
          cache: "no-store",
          credentials: "omit",
          referrerPolicy: "no-referrer",
        });
        if (controller.signal.aborted) throw new ProjectApiError("unavailable");
        if (!ok.has(response.status)) {
          const code: ProjectErrorCode =
            (
              {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                413: "invalid_request",
                415: "invalid_request",
                422: "invalid_request",
              } as Record<number, ProjectErrorCode>
            )[response.status] ?? "unavailable";
          void response.body?.cancel().catch(() => undefined);
          throw new ProjectApiError(code);
        }
        try {
          return await boundedJson(response, controller.signal);
        } catch {
          throw new ProjectApiError("invalid_response");
        }
      })();
      return await Promise.race([operation, deadline]);
    } catch (error) {
      throw error instanceof ProjectApiError
        ? error
        : new ProjectApiError("unavailable");
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  async function requestBytes(
    path: string,
  ): Promise<{ bytes: ArrayBuffer; contentType: string }> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new ProjectApiError("unavailable"));
      }, 30000);
    });
    try {
      const operation = (async () => {
        const response = await fetcher(base + path, {
          method: "GET",
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/octet-stream",
          },
          signal: controller.signal,
          redirect: "error",
          cache: "no-store",
          credentials: "omit",
          referrerPolicy: "no-referrer",
        });
        if (controller.signal.aborted) throw new ProjectApiError("unavailable");
        if (response.status !== 200) {
          const code: ProjectErrorCode =
            (
              {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
              } as Record<number, ProjectErrorCode>
            )[response.status] ?? "unavailable";
          void response.body?.cancel().catch(() => undefined);
          throw new ProjectApiError(code);
        }
        try {
          const bytes = await boundedBytes(
            response,
            controller.signal,
            ATTACHMENT_MAX,
          );
          return {
            bytes,
            contentType:
              (response.headers.get("content-type") ?? "")
                .split(";")[0]
                .trim() || "application/octet-stream",
          };
        } catch {
          throw new ProjectApiError("invalid_response");
        }
      })();
      return await Promise.race([operation, deadline]);
    } catch (error) {
      throw error instanceof ProjectApiError
        ? error
        : new ProjectApiError("unavailable");
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  return {
    async listActivations(projectId, limit = 50, beforeRevision = null) {
      if (
        !isIdentifier(projectId) ||
        !positive(limit) ||
        limit > 100 ||
        !(beforeRevision === null || beforeRevision === undefined
          ? true
          : positive(beforeRevision))
      )
        throw new ProjectApiError("invalid_request");
      const params = new URLSearchParams({ limit: String(limit) });
      if (beforeRevision != null)
        params.set("before_revision", String(beforeRevision));
      const value = await requestJson(
        `/projects/${projectId}/activations?${params.toString()}`,
        { method: "GET" },
      );
      try {
        if (
          !isObject(value) ||
          value.project_id !== projectId ||
          !Array.isArray(value.items) ||
          value.items.length > 100 ||
          !(
            value.next_before_revision === null ||
            positive(value.next_before_revision)
          )
        )
          throw new Error();
        return {
          project_id: projectId,
          items: value.items.map(parseSummary),
          next_before_revision: value.next_before_revision,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getActivationOperation(projectId, operationId) {
      if (!isIdentifier(projectId) || !isIdentifier(operationId))
        throw new ProjectApiError("invalid_request");
      const value = await requestJson(
        `/projects/${projectId}/activations/${operationId}`,
        { method: "GET" },
      );
      try {
        const summary = parseSummary(value);
        if (
          summary.project_id !== projectId ||
          summary.operation_id !== operationId
        )
          throw new Error();
        return summary;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async listAttachments(projectId, operationId) {
      if (!isIdentifier(projectId) || !isIdentifier(operationId))
        throw new ProjectApiError("invalid_request");
      const value = await requestJson(
        `/projects/${projectId}/activations/${operationId}/attachments`,
        { method: "GET" },
      );
      try {
        if (!Array.isArray(value) || value.length > 10) throw new Error();
        return value.map(parseAttachment);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async uploadAttachment(
      projectId,
      operationId,
      attachmentId,
      content,
      displayName = "Approval evidence",
    ) {
      if (
        !isIdentifier(projectId) ||
        !isIdentifier(operationId) ||
        !isIdentifier(attachmentId) ||
        typeof displayName !== "string" ||
        displayName.trim().length < 1 ||
        displayName.length > 128
      )
        throw new ProjectApiError("invalid_request");
      const bytes = await attachmentBytes(content);
      const params = new URLSearchParams({ display_name: displayName });
      const value = await requestJson(
        `/projects/${projectId}/activations/${operationId}/attachments/${attachmentId}?${params.toString()}`,
        {
          method: "PUT",
          body: bytes,
          contentType: "application/octet-stream",
          ok: new Set([201]),
        },
      );
      try {
        const metadata = parseAttachment(value);
        if (metadata.attachment_id !== attachmentId) throw new Error();
        if (metadata.size !== bytes.byteLength) throw new Error();
        return metadata;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async downloadAttachment(projectId, operationId, attachmentId) {
      if (
        !isIdentifier(projectId) ||
        !isIdentifier(operationId) ||
        !isIdentifier(attachmentId)
      )
        throw new ProjectApiError("invalid_request");
      const downloaded = await requestBytes(
        `/projects/${projectId}/activations/${operationId}/attachments/${attachmentId}`,
      );
      return {
        bytes: downloaded.bytes,
        filename: "deployment-evidence.bin",
        contentType: downloaded.contentType,
      };
    },
  };
}
