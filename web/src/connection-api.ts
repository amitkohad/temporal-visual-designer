/** Signed Dev HTTP connection + mock-fixture client. Tokens stay in Authorization. */
import {
  ProjectApiError,
  isIdentifier,
  isObject,
  type JsonObject,
  type JsonValue,
  type ProjectConnection,
  type ProjectErrorCode,
} from "./project-api";

export type HttpMethod =
  | "GET"
  | "HEAD"
  | "POST"
  | "PUT"
  | "PATCH"
  | "DELETE"
  | "OPTIONS";

export interface ConnectionMetadata {
  connection_id: string;
  connection_revision: string;
  revision: number;
  connector_type: "http";
  connector_version: "1.0.0";
  definition_digest: string;
  has_secret_reference: boolean;
  actor_id: string;
  created_at: string;
}

export interface ConnectionPage {
  items: ConnectionMetadata[];
  next_before_revision: number | null;
}

export interface HTTPConnectionWrite {
  connector_type: "http";
  connector_version: "1.0.0";
  base_origin: string;
  allowed_origins: string[];
  secret_reference?: string | null;
}

export interface HTTPMockFixtureWrite {
  method: HttpMethod;
  path: string;
  json_body?: JsonObject | null;
  status_code: number;
  response_json_body?: JsonObject | null;
}

export interface HTTPMockFixtureMetadata {
  connection_id: string;
  connection_revision: string;
  fixture_digest: string;
  stored_bytes: number;
}

export interface HTTPMockFixturePage {
  items: HTTPMockFixtureMetadata[];
  next_after_connection_id: string | null;
  next_after_connection_revision: string | null;
}

export interface ConnectionPickOption {
  connection_id: string;
  connection_revision: string;
  label: string;
  fixture_digest?: string;
}

export interface ConnectionClient {
  putConnection(
    connectionId: string,
    revision: string,
    body: HTTPConnectionWrite,
  ): Promise<ConnectionMetadata>;
  getConnection(
    connectionId: string,
    revision: string,
  ): Promise<ConnectionMetadata>;
  listConnectionRevisions(
    connectionId: string,
    limit?: number,
    beforeRevision?: number | null,
  ): Promise<ConnectionPage>;
  putMockFixture(
    connectionId: string,
    revision: string,
    body: HTTPMockFixtureWrite,
  ): Promise<HTTPMockFixtureMetadata>;
  getMockFixture(
    connectionId: string,
    revision: string,
  ): Promise<HTTPMockFixtureMetadata>;
  listMockFixtures(
    limit?: number,
    after?: { connection_id: string; connection_revision: string } | null,
  ): Promise<HTTPMockFixturePage>;
}

const MAX_BYTES = 6 * 1024 * 1024;
const METHODS = new Set<HttpMethod>([
  "GET",
  "HEAD",
  "POST",
  "PUT",
  "PATCH",
  "DELETE",
  "OPTIONS",
]);

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function isDigest(value: unknown): value is string {
  return typeof value === "string" && /^sha256:[a-f0-9]{64}$/.test(value);
}

function parseScope(
  value: unknown,
  organizationId: string,
  environmentId: string,
): void {
  if (
    !isObject(value) ||
    value.organization_id !== organizationId ||
    value.environment_id !== environmentId
  )
    throw new Error();
}

function parseConnectionMetadata(
  value: unknown,
  organizationId: string,
  environmentId: string,
): ConnectionMetadata {
  if (
    !isObject(value) ||
    !isIdentifier(value.connection_id) ||
    !isIdentifier(value.connection_revision) ||
    !positive(value.revision) ||
    value.connector_type !== "http" ||
    value.connector_version !== "1.0.0" ||
    !isDigest(value.definition_digest) ||
    typeof value.has_secret_reference !== "boolean" ||
    !isIdentifier(value.actor_id) ||
    typeof value.created_at !== "string" ||
    value.created_at.length < 1 ||
    value.created_at.length > 64
  )
    throw new Error();
  parseScope(value.scope, organizationId, environmentId);
  return {
    connection_id: value.connection_id,
    connection_revision: value.connection_revision,
    revision: value.revision,
    connector_type: "http",
    connector_version: "1.0.0",
    definition_digest: value.definition_digest,
    has_secret_reference: value.has_secret_reference,
    actor_id: value.actor_id,
    created_at: value.created_at,
  };
}

function parseFixtureMetadata(
  value: unknown,
  organizationId: string,
  environmentId: string,
): HTTPMockFixtureMetadata {
  if (
    !isObject(value) ||
    !isIdentifier(value.connection_id) ||
    !isIdentifier(value.connection_revision) ||
    !isDigest(value.fixture_digest) ||
    !positive(value.stored_bytes) ||
    value.stored_bytes > 12582912
  )
    throw new Error();
  parseScope(value.scope, organizationId, environmentId);
  return {
    connection_id: value.connection_id,
    connection_revision: value.connection_revision,
    fixture_digest: value.fixture_digest,
    stored_bytes: value.stored_bytes,
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
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}

/** Validate HTTPS DNS origin shape without resolving DNS or dialing. */
export function validateHttpsOrigin(value: string): string | null {
  if (typeof value !== "string" || value.length < 8 || value.length > 2048)
    return "Enter an https:// DNS origin.";
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      (url.pathname !== "/" && url.pathname !== "") ||
      url.search ||
      url.hash ||
      !url.hostname ||
      url.hostname.endsWith(".") ||
      !url.hostname.includes(".") ||
      url.hostname.length > 253
    )
      return "Origins must be https DNS hosts with no path, query, or credentials.";
    if (/^\d{1,3}(\.\d{1,3}){3}$/.test(url.hostname) || url.hostname.includes(":"))
      return "IP literals are not allowed; use a DNS hostname.";
    return null;
  } catch {
    return "Enter a valid https:// DNS origin.";
  }
}

export function validateMockFixtureWrite(
  body: HTTPMockFixtureWrite,
): string | null {
  if (!METHODS.has(body.method)) return "Choose a supported HTTP method.";
  if (
    typeof body.path !== "string" ||
    !body.path.startsWith("/") ||
    body.path.startsWith("//") ||
    body.path.length > 4096
  )
    return "Path must be origin-relative and start with a single /.";
  if (
    !Number.isSafeInteger(body.status_code) ||
    body.status_code < 200 ||
    body.status_code > 599
  )
    return "Status code must be an integer from 200 to 599.";
  if (
    (body.method === "GET" || body.method === "HEAD") &&
    body.json_body != null
  )
    return "GET and HEAD cannot include a JSON request body.";
  if (
    (body.method === "HEAD" ||
      body.status_code === 204 ||
      body.status_code === 304) &&
    body.response_json_body != null
  )
    return "This response cannot include a JSON body.";
  return null;
}

export function pickOptionsFromFixtures(
  fixtures: HTTPMockFixtureMetadata[],
): ConnectionPickOption[] {
  return fixtures.map((item) => ({
    connection_id: item.connection_id,
    connection_revision: item.connection_revision,
    fixture_digest: item.fixture_digest,
    label: `${item.connection_id} / ${item.connection_revision} · mock ${item.fixture_digest.slice(0, 18)}…`,
  }));
}

export function pickOptionsFromRevisions(
  items: ConnectionMetadata[],
): ConnectionPickOption[] {
  return items.map((item) => ({
    connection_id: item.connection_id,
    connection_revision: item.connection_revision,
    label: `${item.connection_id} / ${item.connection_revision} · rev ${item.revision}`,
  }));
}

export function mergePickOptions(
  ...groups: ConnectionPickOption[][]
): ConnectionPickOption[] {
  const seen = new Set<string>();
  const out: ConnectionPickOption[] = [];
  for (const group of groups) {
    for (const item of group) {
      const key = `${item.connection_id}\0${item.connection_revision}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(item);
    }
  }
  return out;
}

export function createConnectionClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): ConnectionClient {
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
  const root = `/api/v1/organizations/${organizationId}/environments/${environmentId}`;

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
        const response = await fetcher(root + path, {
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
    async putConnection(connectionId, revision, body) {
      if (!isIdentifier(connectionId) || !isIdentifier(revision))
        throw new ProjectApiError("invalid_request");
      if (validateHttpsOrigin(body.base_origin))
        throw new ProjectApiError("invalid_request");
      const payload: JsonObject = {
        connector_type: "http",
        connector_version: "1.0.0",
        base_origin: body.base_origin,
        allowed_origins: body.allowed_origins,
      };
      if (body.secret_reference != null && body.secret_reference !== "") {
        if (!isIdentifier(body.secret_reference))
          throw new ProjectApiError("invalid_request");
        payload.secret_reference = body.secret_reference;
      }
      const value = await request(
        `/connections/${connectionId}/revisions/${revision}`,
        "PUT",
        JSON.stringify(payload),
      );
      try {
        return parseConnectionMetadata(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async getConnection(connectionId, revision) {
      if (!isIdentifier(connectionId) || !isIdentifier(revision))
        throw new ProjectApiError("invalid_request");
      const value = await request(
        `/connections/${connectionId}/revisions/${revision}`,
      );
      try {
        return parseConnectionMetadata(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async listConnectionRevisions(connectionId, limit = 25, beforeRevision = null) {
      if (
        !isIdentifier(connectionId) ||
        !Number.isSafeInteger(limit) ||
        limit < 1 ||
        limit > 50 ||
        (beforeRevision != null && !positive(beforeRevision))
      )
        throw new ProjectApiError("invalid_request");
      const query =
        `?limit=${limit}` +
        (beforeRevision == null ? "" : `&before_revision=${beforeRevision}`);
      const value = await request(
        `/connections/${connectionId}/revisions${query}`,
      );
      try {
        if (
          !isObject(value) ||
          !Array.isArray(value.items) ||
          value.items.length > 50 ||
          !(
            value.next_before_revision === null ||
            positive(value.next_before_revision)
          )
        )
          throw new Error();
        return {
          items: value.items.map((item) =>
            parseConnectionMetadata(item, organizationId, environmentId),
          ),
          next_before_revision: value.next_before_revision,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async putMockFixture(connectionId, revision, body) {
      if (!isIdentifier(connectionId) || !isIdentifier(revision))
        throw new ProjectApiError("invalid_request");
      if (validateMockFixtureWrite(body))
        throw new ProjectApiError("invalid_request");
      const payload: JsonObject = {
        method: body.method,
        path: body.path,
        status_code: body.status_code,
        json_body: (body.json_body ?? null) as JsonValue,
        response_json_body: (body.response_json_body ?? null) as JsonValue,
      };
      const value = await request(
        `/connections/${connectionId}/revisions/${revision}/mock-fixture`,
        "PUT",
        JSON.stringify(payload),
      );
      try {
        return parseFixtureMetadata(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async getMockFixture(connectionId, revision) {
      if (!isIdentifier(connectionId) || !isIdentifier(revision))
        throw new ProjectApiError("invalid_request");
      const value = await request(
        `/connections/${connectionId}/revisions/${revision}/mock-fixture`,
      );
      try {
        return parseFixtureMetadata(value, organizationId, environmentId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async listMockFixtures(limit = 25, after = null) {
      if (
        !Number.isSafeInteger(limit) ||
        limit < 1 ||
        limit > 50 ||
        (after != null &&
          (!isIdentifier(after.connection_id) ||
            !isIdentifier(after.connection_revision)))
      )
        throw new ProjectApiError("invalid_request");
      const params = new URLSearchParams({ limit: String(limit) });
      if (after) {
        params.set("after_connection_id", after.connection_id);
        params.set("after_connection_revision", after.connection_revision);
      }
      const value = await request(`/mock-fixtures?${params.toString()}`);
      try {
        if (
          !isObject(value) ||
          !Array.isArray(value.items) ||
          value.items.length > 50 ||
          !(
            value.next_after_connection_id === null ||
            isIdentifier(value.next_after_connection_id)
          ) ||
          !(
            value.next_after_connection_revision === null ||
            isIdentifier(value.next_after_connection_revision)
          ) ||
          (value.next_after_connection_id === null) !==
            (value.next_after_connection_revision === null)
        )
          throw new Error();
        return {
          items: value.items.map((item) =>
            parseFixtureMetadata(item, organizationId, environmentId),
          ),
          next_after_connection_id: value.next_after_connection_id,
          next_after_connection_revision: value.next_after_connection_revision,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}

export function createConnectionWrite(
  baseOrigin: string,
  allowedOrigins: string[],
  secretReference?: string,
): HTTPConnectionWrite {
  const origins = [...new Set(allowedOrigins.map((item) => item.trim()).filter(Boolean))];
  if (!origins.includes(baseOrigin.trim())) origins.unshift(baseOrigin.trim());
  return {
    connector_type: "http",
    connector_version: "1.0.0",
    base_origin: baseOrigin.trim(),
    allowed_origins: origins,
    ...(secretReference && secretReference.trim()
      ? { secret_reference: secretReference.trim() }
      : {}),
  };
}
