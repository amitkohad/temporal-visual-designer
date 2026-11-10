/** Private, bounded same-origin project API. Tokens never leave request headers. */
export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | JsonObject;
export interface JsonObject {
  [key: string]: JsonValue;
}
export interface ProjectConnection {
  organizationId: string;
  environmentId: string;
  token: string;
}
export interface ProjectSummary {
  project_id: string;
  name: string;
  head_revision: number;
}
export interface ProjectRevision {
  project_id: string;
  revision: number;
  parent_revision: number | null;
  summary: string;
  author_id: string;
  created_at: string;
  content: JsonObject;
}
export type ProjectErrorCode =
  | "invalid_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "unavailable"
  | "invalid_response";
export class ProjectApiError extends Error {
  readonly code: ProjectErrorCode;
  constructor(code: ProjectErrorCode) {
    super("Project request failed");
    this.name = "ProjectApiError";
    this.code = [
      "invalid_request",
      "unauthorized",
      "forbidden",
      "not_found",
      "conflict",
      "unavailable",
      "invalid_response",
    ].includes(code)
      ? code
      : "unavailable";
  }
}
export interface ProjectClient {
  listProjects(): Promise<ProjectSummary[]>;
  readProject(id: string): Promise<ProjectRevision>;
  saveProject(
    id: string,
    expectedRevision: number,
    content: JsonObject,
    summary: string,
  ): Promise<ProjectRevision>;
}
const MAX_BYTES = 6 * 1024 * 1024;
const encoder = new TextEncoder();
export function isIdentifier(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 1 &&
    value.length <= 128 &&
    value !== "." &&
    value !== ".." &&
    /^[A-Za-z0-9_.-]+$/.test(value)
  );
}
export function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}
function canonical(value: unknown, depth = 0): string {
  if (depth > 100) throw new Error();
  if (value === null || typeof value === "boolean" || typeof value === "string")
    return JSON.stringify(value);
  if (
    typeof value === "number" &&
    Number.isFinite(value) &&
    (!Number.isInteger(value) || Number.isSafeInteger(value))
  )
    return JSON.stringify(value);
  if (Array.isArray(value))
    return (
      "[" + value.map((item) => canonical(item, depth + 1)).join(",") + "]"
    );
  if (isObject(value) && Object.getPrototypeOf(value) === Object.prototype) {
    return (
      "{" +
      Object.keys(value)
        .sort()
        .map(
          (key) => JSON.stringify(key) + ":" + canonical(value[key], depth + 1),
        )
        .join(",") +
      "}"
    );
  }
  throw new Error();
}
function snapshot(value: unknown): JsonObject {
  if (!isObject(value)) throw new Error();
  const text = canonical(value);
  if (encoder.encode(text).byteLength > MAX_BYTES) throw new Error();
  return JSON.parse(text) as JsonObject;
}
function revision(value: unknown, projectId: string): ProjectRevision {
  if (
    !isObject(value) ||
    value.project_id !== projectId ||
    !positive(value.revision) ||
    !(value.parent_revision === null || positive(value.parent_revision)) ||
    (value.parent_revision !== null &&
      value.parent_revision !== value.revision - 1) ||
    (value.parent_revision === null && value.revision !== 1) ||
    typeof value.summary !== "string" ||
    value.summary.length > 1000 ||
    !isIdentifier(value.author_id) ||
    typeof value.created_at !== "string" ||
    value.created_at.length > 64 ||
    !Number.isFinite(Date.parse(value.created_at)) ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(
      value.created_at,
    ) ||
    new Date(value.created_at).toISOString().slice(0, 19) !==
      value.created_at.slice(0, 19) ||
    !isObject(value.content) ||
    value.content.schema_version !== "0.1" ||
    !Array.isArray(value.content.workflows) ||
    value.content.workflows.length > 100
  )
    throw new Error();
  return {
    project_id: projectId,
    revision: value.revision,
    parent_revision: value.parent_revision,
    summary: value.summary,
    author_id: value.author_id,
    created_at: value.created_at,
    content: snapshot(value.content),
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
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  // Python distinguishes integer and float JSON values in its canonical hashes.
  // Reject a wire number that JS would round or re-encode with a different token,
  // including 1.0 -> 1, rather than silently altering untouched project content.
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '"') {
      index += 1;
      while (index < text.length && text[index] !== '"') {
        if (text[index] === "\\") index += 1;
        index += 1;
      }
    } else if (text[index] === "-" || /[0-9]/.test(text[index])) {
      const token = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(
        text.slice(index),
      )?.[0];
      if (!token || JSON.stringify(Number(token)) !== token) throw new Error();
      index += token.length - 1;
    }
  }
  return JSON.parse(text);
}

export function createProjectClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): ProjectClient {
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
  const base = `/api/v1/organizations/${organizationId}/environments/${environmentId}/projects`;
  async function request(
    path: string,
    method: "GET" | "PUT" = "GET",
    body?: string,
  ): Promise<unknown> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new ProjectApiError("unavailable"));
      }, 10000);
    });
    try {
      const operation = (async () => {
        const response = await fetcher(base + path, {
          method,
          headers: {
            Authorization: `Bearer ${token}`,
            Accept: "application/json",
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
          },
          ...(body === undefined ? {} : { body }),
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
    async listProjects() {
      const value = await request("?limit=100&offset=0");
      try {
        if (!Array.isArray(value) || value.length > 100) throw new Error();
        const ids = new Set<string>();
        return value.map((item) => {
          if (
            !isObject(item) ||
            !isIdentifier(item.project_id) ||
            ids.has(item.project_id) ||
            typeof item.name !== "string" ||
            item.name.length < 1 ||
            item.name.length > 200 ||
            !positive(item.head_revision) ||
            !isObject(item.scope) ||
            item.scope.organization_id !== organizationId ||
            item.scope.environment_id !== environmentId
          )
            throw new Error();
          ids.add(item.project_id);
          return {
            project_id: item.project_id,
            name: item.name,
            head_revision: item.head_revision,
          };
        });
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async readProject(id) {
      if (!isIdentifier(id)) throw new ProjectApiError("invalid_request");
      const value = await request("/" + id);
      try {
        return revision(value, id);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async saveProject(id, expectedRevision, content, summary) {
      let saved: JsonObject;
      let body: string;
      try {
        if (
          !isIdentifier(id) ||
          !positive(expectedRevision) ||
          !Number.isSafeInteger(expectedRevision + 1) ||
          typeof summary !== "string" ||
          !summary.trim() ||
          summary.length > 1000
        )
          throw new Error();
        saved = snapshot(content);
        if (saved.schema_version !== "0.1" || !Array.isArray(saved.workflows))
          throw new Error();
        body = canonical({
          expected_revision: expectedRevision,
          summary,
          content: saved,
        });
        if (encoder.encode(body).byteLength > MAX_BYTES) throw new Error();
      } catch {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request("/" + id, "PUT", body);
      try {
        const ack = revision(value, id);
        if (
          ack.revision !== expectedRevision + 1 ||
          ack.parent_revision !== expectedRevision ||
          canonical(ack.content) !== canonical(saved)
        )
          throw new Error();
        return ack;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}
