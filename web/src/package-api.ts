/**
 * Project package export / validate / stage / materialize client.
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
export { ProjectApiError as PackageApiError };

export interface PackageFile {
  path: string;
  size: number;
  checksum: string;
  media_type: "application/json" | "application/zip";
}

export interface ActivityRef {
  activity_id: string;
  version: string;
  artifact_digest: string;
}

export interface PackageManifest {
  package_schema_version: "0.1";
  organization_id: string;
  project_id: string;
  release_id: string;
  source_revision: number;
  created_at: string;
  runtime_version: "proof.v1";
  activity_refs: ActivityRef[];
  content_index: PackageFile[];
  content_digest: string;
  executable_digest: string;
  package_digest: string;
}

export interface PackageImportMetadata {
  operation_id: string;
  revision: number;
  project_id: string;
  release_id: string;
  package_digest: string;
  source_revision: number;
  actor_id: string;
  created_at: string;
  state: "staged";
}

export interface PackageImportPage {
  items: PackageImportMetadata[];
  next_before_revision: number | null;
}

export interface MaterializationParams {
  operationId: string;
  expectedProjectRevision: number;
  configurationRevision: string;
  runtimeVersion?: "proof.v1";
}

export interface MaterializationReceipt {
  operation_id: string;
  import_operation_id: string;
  project_id: string;
  package_digest: string;
  source_release_id: string;
  source_revision: number;
  release_id: string;
  revision: number;
  configuration_revision: string;
  runtime_version: "proof.v1";
  actor_id: string;
  created_at: string;
  state: "materialized";
}

export interface ExportedPackage {
  bytes: ArrayBuffer;
  digest: string | null;
  filename: string;
}

export interface PackageClient {
  exportPackage(projectId: string, releaseId: string): Promise<ExportedPackage>;
  validatePackage(
    archive: ArrayBuffer | Uint8Array | Blob,
  ): Promise<PackageManifest>;
  stageImport(
    operationId: string,
    archive: ArrayBuffer | Uint8Array | Blob,
  ): Promise<PackageImportMetadata>;
  listImports(
    limit?: number,
    beforeRevision?: number | null,
  ): Promise<PackageImportPage>;
  getImport(operationId: string): Promise<PackageImportMetadata>;
  getImportManifest(operationId: string): Promise<PackageManifest>;
  materialize(
    importOperationId: string,
    params: MaterializationParams,
  ): Promise<MaterializationReceipt>;
  getMaterialization(
    importOperationId: string,
    operationId: string,
  ): Promise<MaterializationReceipt>;
}

const UPLOAD_MAX = 6 * 1024 * 1024;
const DOWNLOAD_MAX = 16 * 1024 * 1024;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/;
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

function isActivityRef(value: unknown): value is ActivityRef {
  return (
    isObject(value) &&
    isIdentifier(value.activity_id) &&
    typeof value.version === "string" &&
    VERSION.test(value.version) &&
    isDigest(value.artifact_digest)
  );
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
    (!/^\d+$/.test(length) || Number(length) > UPLOAD_MAX)
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
      if (count > UPLOAD_MAX) throw new Error();
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
  if (media !== "application/zip" && media !== "application/octet-stream") {
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

async function archiveBytes(
  archive: ArrayBuffer | Uint8Array | Blob,
): Promise<ArrayBuffer> {
  if (archive instanceof ArrayBuffer) {
    if (archive.byteLength < 1 || archive.byteLength > UPLOAD_MAX)
      throw new ProjectApiError("invalid_request");
    return archive;
  }
  if (archive instanceof Uint8Array) {
    if (archive.byteLength < 1 || archive.byteLength > UPLOAD_MAX)
      throw new ProjectApiError("invalid_request");
    return archive.buffer.slice(
      archive.byteOffset,
      archive.byteOffset + archive.byteLength,
    ) as ArrayBuffer;
  }
  if (typeof Blob !== "undefined" && archive instanceof Blob) {
    if (archive.size < 1 || archive.size > UPLOAD_MAX)
      throw new ProjectApiError("invalid_request");
    return archive.arrayBuffer();
  }
  throw new ProjectApiError("invalid_request");
}

function parseManifest(value: unknown): PackageManifest {
  if (
    !isObject(value) ||
    value.package_schema_version !== "0.1" ||
    !isIdentifier(value.organization_id) ||
    !isIdentifier(value.project_id) ||
    !isIdentifier(value.release_id) ||
    !positive(value.source_revision) ||
    !isTimestamp(value.created_at) ||
    value.runtime_version !== "proof.v1" ||
    !Array.isArray(value.activity_refs) ||
    value.activity_refs.length > 100 ||
    !value.activity_refs.every(isActivityRef) ||
    !Array.isArray(value.content_index) ||
    value.content_index.length > 101 ||
    !value.content_index.every(
      (item) =>
        isObject(item) &&
        typeof item.path === "string" &&
        item.path.length >= 1 &&
        item.path.length <= 512 &&
        nonNegative(item.size) &&
        isDigest(item.checksum) &&
        (item.media_type === "application/json" ||
          item.media_type === "application/zip"),
    ) ||
    !isDigest(value.content_digest) ||
    !isDigest(value.executable_digest) ||
    !isDigest(value.package_digest)
  )
    throw new Error();
  const activityRefs: ActivityRef[] = [];
  for (const ref of value.activity_refs) {
    if (!isActivityRef(ref)) throw new Error();
    activityRefs.push({
      activity_id: ref.activity_id,
      version: ref.version,
      artifact_digest: ref.artifact_digest,
    });
  }
  const contentIndex: PackageFile[] = [];
  for (const item of value.content_index) {
    if (
      !isObject(item) ||
      typeof item.path !== "string" ||
      !nonNegative(item.size) ||
      !isDigest(item.checksum) ||
      (item.media_type !== "application/json" &&
        item.media_type !== "application/zip")
    )
      throw new Error();
    contentIndex.push({
      path: item.path,
      size: item.size,
      checksum: item.checksum,
      media_type: item.media_type,
    });
  }
  return {
    package_schema_version: "0.1",
    organization_id: value.organization_id,
    project_id: value.project_id,
    release_id: value.release_id,
    source_revision: value.source_revision,
    created_at: value.created_at,
    runtime_version: "proof.v1",
    activity_refs: activityRefs,
    content_index: contentIndex,
    content_digest: value.content_digest,
    executable_digest: value.executable_digest,
    package_digest: value.package_digest,
  };
}

function parseImport(value: unknown): PackageImportMetadata {
  if (
    !isObject(value) ||
    !isIdentifier(value.operation_id) ||
    !positive(value.revision) ||
    !isIdentifier(value.project_id) ||
    !isIdentifier(value.release_id) ||
    !isDigest(value.package_digest) ||
    !positive(value.source_revision) ||
    typeof value.actor_id !== "string" ||
    value.actor_id.length < 1 ||
    value.actor_id.length > 128 ||
    !isTimestamp(value.created_at) ||
    value.state !== "staged"
  )
    throw new Error();
  return {
    operation_id: value.operation_id,
    revision: value.revision,
    project_id: value.project_id,
    release_id: value.release_id,
    package_digest: value.package_digest,
    source_revision: value.source_revision,
    actor_id: value.actor_id,
    created_at: value.created_at,
    state: "staged",
  };
}

function parseMaterialization(value: unknown): MaterializationReceipt {
  if (
    !isObject(value) ||
    !isIdentifier(value.operation_id) ||
    !isIdentifier(value.import_operation_id) ||
    !isIdentifier(value.project_id) ||
    !isDigest(value.package_digest) ||
    !isIdentifier(value.source_release_id) ||
    !positive(value.source_revision) ||
    !isIdentifier(value.release_id) ||
    !positive(value.revision) ||
    !isIdentifier(value.configuration_revision) ||
    value.runtime_version !== "proof.v1" ||
    typeof value.actor_id !== "string" ||
    value.actor_id.length < 1 ||
    value.actor_id.length > 128 ||
    !isTimestamp(value.created_at) ||
    value.state !== "materialized"
  )
    throw new Error();
  return {
    operation_id: value.operation_id,
    import_operation_id: value.import_operation_id,
    project_id: value.project_id,
    package_digest: value.package_digest,
    source_release_id: value.source_release_id,
    source_revision: value.source_revision,
    release_id: value.release_id,
    revision: value.revision,
    configuration_revision: value.configuration_revision,
    runtime_version: "proof.v1",
    actor_id: value.actor_id,
    created_at: value.created_at,
    state: "materialized",
  };
}

/** Trigger a private browser download for exported ZIP bytes. */
export function downloadPackageBytes(
  exported: ExportedPackage,
  filename = exported.filename,
): void {
  const blob = new Blob([exported.bytes], { type: "application/zip" });
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

export function createPackageClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): PackageClient {
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
      method: "GET" | "POST" | "PUT";
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

  async function requestZip(
    path: string,
  ): Promise<{ bytes: ArrayBuffer; digest: string | null }> {
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
            Accept: "application/zip",
          },
          signal: controller.signal,
          redirect: "error",
          cache: "no-store",
          credentials: "omit",
          referrerPolicy: "no-referrer",
        });
        if (controller.signal.aborted) throw new ProjectApiError("unavailable");
        if (!response.ok) {
          const code: ProjectErrorCode =
            (
              {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                422: "invalid_request",
              } as Record<number, ProjectErrorCode>
            )[response.status] ?? "unavailable";
          void response.body?.cancel().catch(() => undefined);
          throw new ProjectApiError(code);
        }
        const digestHeader = response.headers.get("X-Package-Digest");
        const digest =
          digestHeader && isDigest(digestHeader) ? digestHeader : null;
        try {
          const bytes = await boundedBytes(
            response,
            controller.signal,
            DOWNLOAD_MAX,
          );
          return { bytes, digest };
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
    async exportPackage(projectId, releaseId) {
      if (!isIdentifier(projectId) || !isIdentifier(releaseId))
        throw new ProjectApiError("invalid_request");
      const result = await requestZip(
        `/projects/${encodeURIComponent(projectId)}/releases/${encodeURIComponent(releaseId)}/package`,
      );
      return {
        bytes: result.bytes,
        digest: result.digest,
        filename: "nexus-flow-project.zip",
      };
    },

    async validatePackage(archive) {
      const bytes = await archiveBytes(archive);
      const value = await requestJson("/packages/validation", {
        method: "POST",
        body: bytes,
        contentType: "application/zip",
        ok: new Set([200]),
      });
      try {
        return parseManifest(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async stageImport(operationId, archive) {
      if (!isIdentifier(operationId))
        throw new ProjectApiError("invalid_request");
      const bytes = await archiveBytes(archive);
      const value = await requestJson(
        `/package-imports/${encodeURIComponent(operationId)}`,
        {
          method: "PUT",
          body: bytes,
          contentType: "application/zip",
          ok: new Set([201]),
        },
      );
      try {
        return parseImport(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async listImports(limit = 50, beforeRevision = null) {
      if (
        !positive(limit) ||
        limit > 100 ||
        (beforeRevision !== null &&
          beforeRevision !== undefined &&
          !positive(beforeRevision))
      )
        throw new ProjectApiError("invalid_request");
      const params = new URLSearchParams({ limit: String(limit) });
      if (beforeRevision != null)
        params.set("before_revision", String(beforeRevision));
      const value = await requestJson(`/package-imports?${params.toString()}`, {
        method: "GET",
      });
      try {
        if (
          !isObject(value) ||
          !Array.isArray(value.items) ||
          value.items.length > 100 ||
          !(
            value.next_before_revision === null ||
            positive(value.next_before_revision)
          )
        )
          throw new Error();
        return {
          items: value.items.map(parseImport),
          next_before_revision: value.next_before_revision,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getImport(operationId) {
      if (!isIdentifier(operationId))
        throw new ProjectApiError("invalid_request");
      const value = await requestJson(
        `/package-imports/${encodeURIComponent(operationId)}`,
        { method: "GET" },
      );
      try {
        return parseImport(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getImportManifest(operationId) {
      if (!isIdentifier(operationId))
        throw new ProjectApiError("invalid_request");
      const value = await requestJson(
        `/package-imports/${encodeURIComponent(operationId)}/manifest`,
        { method: "GET" },
      );
      try {
        return parseManifest(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async materialize(importOperationId, params) {
      const runtimeVersion = params.runtimeVersion ?? "proof.v1";
      if (
        !isIdentifier(importOperationId) ||
        !isIdentifier(params.operationId) ||
        !nonNegative(params.expectedProjectRevision) ||
        !isIdentifier(params.configurationRevision) ||
        runtimeVersion !== "proof.v1"
      )
        throw new ProjectApiError("invalid_request");
      const value = await requestJson(
        `/package-imports/${encodeURIComponent(importOperationId)}/materializations`,
        {
          method: "POST",
          body: JSON.stringify({
            operation_id: params.operationId,
            expected_project_revision: params.expectedProjectRevision,
            configuration_revision: params.configurationRevision,
            runtime_version: runtimeVersion,
          }),
          contentType: "application/json",
          ok: new Set([201]),
        },
      );
      try {
        return parseMaterialization(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getMaterialization(importOperationId, operationId) {
      if (!isIdentifier(importOperationId) || !isIdentifier(operationId))
        throw new ProjectApiError("invalid_request");
      const value = await requestJson(
        `/package-imports/${encodeURIComponent(importOperationId)}/materializations/${encodeURIComponent(operationId)}`,
        { method: "GET" },
      );
      try {
        return parseMaterialization(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}
