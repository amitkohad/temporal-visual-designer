/**
 * Activity administration client: private ZIP intake, publish, search,
 * resolve, and lifecycle transitions against existing signed APIs.
 * Same privacy posture as project-api (token in Authorization only).
 */
import {
  ProjectApiError,
  isIdentifier,
  isObject,
  type JsonObject,
  type ProjectConnection,
  type ProjectErrorCode,
} from "./project-api";

export type { ProjectConnection, ProjectErrorCode };
export { ProjectApiError as CatalogApiError };

export type CatalogState = "published" | "deprecated" | "withdrawn";
export type TransitionTarget = "deprecated" | "withdrawn";

export interface ActivityRef {
  activity_id: string;
  version: string;
  artifact_digest: string;
}

export interface ActivityImport {
  digest: string;
  manifest: JsonObject;
  state: "validated";
  author_id: string;
  created_at: string;
}

export interface CatalogEntry {
  scope: { organization_id: string; environment_id: string };
  reference: ActivityRef;
  manifest_json: string;
  state: CatalogState;
  generation: number;
  publisher_id: string;
  published_at: string;
  updated_by: string;
  updated_at: string;
}

export interface CatalogClient {
  importBundle(
    archive: ArrayBuffer | Uint8Array | Blob,
  ): Promise<ActivityImport>;
  readImport(digest: string): Promise<ActivityImport>;
  publish(artifactDigest: string): Promise<CatalogEntry>;
  search(
    query?: string,
    limit?: number,
    offset?: number,
  ): Promise<CatalogEntry[]>;
  resolve(reference: ActivityRef): Promise<CatalogEntry>;
  transition(
    reference: ActivityRef,
    target: TransitionTarget,
    expectedGeneration: number,
  ): Promise<CatalogEntry>;
}

const MAX_BYTES = 6 * 1024 * 1024;
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

export function isDigest(value: unknown): value is string {
  return typeof value === "string" && DIGEST.test(value);
}

export function isActivityRef(value: unknown): value is ActivityRef {
  return (
    isObject(value) &&
    isIdentifier(value.activity_id) &&
    typeof value.version === "string" &&
    VERSION.test(value.version) &&
    isDigest(value.artifact_digest)
  );
}

function isTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 64 &&
    TIMESTAMP.test(value) &&
    Number.isFinite(Date.parse(value))
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
    (!/^\d+$/.test(length) || Number(length) > MAX_BYTES)
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
      if (count > MAX_BYTES) throw new Error();
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

function parseImport(value: unknown): ActivityImport {
  if (
    !isObject(value) ||
    !isDigest(value.digest) ||
    !isObject(value.manifest) ||
    value.state !== "validated" ||
    !isIdentifier(value.author_id) ||
    !isTimestamp(value.created_at)
  )
    throw new Error();
  return {
    digest: value.digest,
    manifest: value.manifest,
    state: "validated",
    author_id: value.author_id,
    created_at: value.created_at,
  };
}

function parseEntry(
  value: unknown,
  organizationId: string,
  environmentId: string,
): CatalogEntry {
  if (
    !isObject(value) ||
    !isObject(value.scope) ||
    value.scope.organization_id !== organizationId ||
    value.scope.environment_id !== environmentId ||
    !isActivityRef(value.reference) ||
    typeof value.manifest_json !== "string" ||
    value.manifest_json.length < 2 ||
    value.manifest_json.length > 262_144 ||
    (value.state !== "published" &&
      value.state !== "deprecated" &&
      value.state !== "withdrawn") ||
    !positive(value.generation) ||
    !isIdentifier(value.publisher_id) ||
    !isTimestamp(value.published_at) ||
    !isIdentifier(value.updated_by) ||
    !isTimestamp(value.updated_at)
  )
    throw new Error();
  return {
    scope: {
      organization_id: organizationId,
      environment_id: environmentId,
    },
    reference: {
      activity_id: value.reference.activity_id,
      version: value.reference.version,
      artifact_digest: value.reference.artifact_digest,
    },
    manifest_json: value.manifest_json,
    state: value.state,
    generation: value.generation,
    publisher_id: value.publisher_id,
    published_at: value.published_at,
    updated_by: value.updated_by,
    updated_at: value.updated_at,
  };
}

/** Best-effort display fields from import or catalog manifest JSON. */
export function catalogDisplayLabel(entry: CatalogEntry | ActivityImport): {
  activityId: string;
  version: string;
  displayName: string;
  category: string;
} {
  if ("reference" in entry) {
    let displayName = entry.reference.activity_id;
    let category = "";
    try {
      const parsed = JSON.parse(entry.manifest_json) as unknown;
      if (isObject(parsed) && isObject(parsed.descriptor)) {
        if (typeof parsed.descriptor.display_name === "string")
          displayName = parsed.descriptor.display_name;
        if (typeof parsed.descriptor.category === "string")
          category = parsed.descriptor.category;
      }
    } catch {
      /* keep id */
    }
    return {
      activityId: entry.reference.activity_id,
      version: entry.reference.version,
      displayName,
      category,
    };
  }
  const descriptor = isObject(entry.manifest.descriptor)
    ? entry.manifest.descriptor
    : null;
  const ref =
    descriptor && isObject(descriptor.activity_ref)
      ? descriptor.activity_ref
      : null;
  return {
    activityId:
      ref && isIdentifier(ref.activity_id) ? ref.activity_id : "unknown",
    version:
      ref && typeof ref.version === "string" && VERSION.test(ref.version)
        ? ref.version
        : "?",
    displayName:
      descriptor && typeof descriptor.display_name === "string"
        ? descriptor.display_name
        : "Imported bundle",
    category:
      descriptor && typeof descriptor.category === "string"
        ? descriptor.category
        : "",
  };
}

export function activityRefFromImport(
  imported: ActivityImport,
): ActivityRef | null {
  const descriptor = isObject(imported.manifest.descriptor)
    ? imported.manifest.descriptor
    : null;
  const ref =
    descriptor && isObject(descriptor.activity_ref)
      ? descriptor.activity_ref
      : null;
  return isActivityRef(ref) ? ref : null;
}

export function createCatalogClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): CatalogClient {
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

  async function request(
    path: string,
    init: {
      method: "GET" | "POST";
      body?: BodyInit;
      contentType?: string;
      accept?: string;
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
    try {
      const operation = (async () => {
        const headers: Record<string, string> = {
          Authorization: `Bearer ${token}`,
          Accept: init.accept ?? "application/json",
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
        if (!response.ok) {
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

  return {
    async importBundle(archive) {
      let bytes: ArrayBuffer;
      if (archive instanceof ArrayBuffer) {
        bytes = archive;
      } else if (archive instanceof Uint8Array) {
        bytes = archive.buffer.slice(
          archive.byteOffset,
          archive.byteOffset + archive.byteLength,
        ) as ArrayBuffer;
      } else if (typeof Blob !== "undefined" && archive instanceof Blob) {
        if (archive.size < 1 || archive.size > MAX_BYTES)
          throw new ProjectApiError("invalid_request");
        bytes = await archive.arrayBuffer();
      } else {
        throw new ProjectApiError("invalid_request");
      }
      if (bytes.byteLength < 1 || bytes.byteLength > MAX_BYTES)
        throw new ProjectApiError("invalid_request");
      const value = await request("/activity-imports", {
        method: "POST",
        body: bytes,
        contentType: "application/zip",
      });
      try {
        return parseImport(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async readImport(digest) {
      if (!isDigest(digest)) throw new ProjectApiError("invalid_request");
      const value = await request(
        "/activity-imports/" + encodeURIComponent(digest),
        { method: "GET" },
      );
      try {
        return parseImport(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async publish(artifactDigest) {
      if (!isDigest(artifactDigest))
        throw new ProjectApiError("invalid_request");
      const value = await request("/activities/publish", {
        method: "POST",
        body: JSON.stringify({ artifact_digest: artifactDigest }),
        contentType: "application/json",
      });
      try {
        return parseEntry(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async search(query = "", limit = 50, offset = 0) {
      if (
        typeof query !== "string" ||
        query.length > 200 ||
        !positive(limit) ||
        limit > 100 ||
        !nonNegative(offset) ||
        offset > 10000
      )
        throw new ProjectApiError("invalid_request");
      const params = new URLSearchParams({
        query,
        limit: String(limit),
        offset: String(offset),
      });
      const value = await request("/activities?" + params.toString(), {
        method: "GET",
      });
      try {
        if (!Array.isArray(value) || value.length > 100) throw new Error();
        return value.map((item) =>
          parseEntry(item, organizationId, environmentId),
        );
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async resolve(reference) {
      if (!isActivityRef(reference))
        throw new ProjectApiError("invalid_request");
      const value = await request("/activities/resolve", {
        method: "POST",
        body: JSON.stringify({
          activity_id: reference.activity_id,
          version: reference.version,
          artifact_digest: reference.artifact_digest,
        }),
        contentType: "application/json",
      });
      try {
        return parseEntry(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async transition(reference, target, expectedGeneration) {
      if (
        !isActivityRef(reference) ||
        (target !== "deprecated" && target !== "withdrawn") ||
        !positive(expectedGeneration)
      )
        throw new ProjectApiError("invalid_request");
      const value = await request("/activities/transitions", {
        method: "POST",
        body: JSON.stringify({
          reference: {
            activity_id: reference.activity_id,
            version: reference.version,
            artifact_digest: reference.artifact_digest,
          },
          target,
          expected_generation: expectedGeneration,
        }),
        contentType: "application/json",
      });
      try {
        return parseEntry(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}
