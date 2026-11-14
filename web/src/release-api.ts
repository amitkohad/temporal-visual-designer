/** Publish / activate / start / status client. Same privacy posture as project-api. */
import {
  ProjectApiError,
  isIdentifier,
  isObject,
  type JsonObject,
  type ProjectConnection,
  type ProjectErrorCode,
} from "./project-api";

export type { ProjectConnection, ProjectErrorCode };
export { ProjectApiError as ReleaseApiError };

export interface ProjectRelease {
  project_id: string;
  release_id: string;
  source_revision: number;
  checksum: string;
}

export interface ProjectActivation {
  revision: number;
  release_id: string | null;
  project_id?: string | null;
  operation_id?: string | null;
  previous_release_id?: string | null;
  runtime_version?: string | null;
  configuration_revision?: string | null;
  checksum?: string | null;
  actor_id?: string | null;
  created_at?: string | null;
}

export interface ExecutionAccepted {
  execution_id: string;
  release_id: string;
  configuration_revision: string;
  state: "accepted";
}

export interface ExecutionStatus {
  release_id: string;
  definition_digest: string;
  runtime_version: string;
  node_id: string;
  state: string;
  revision: number;
  completed_nodes: string[];
}

export interface ValidationIssue {
  path: string;
  code: string;
  message: string;
}

export interface ActivateParams {
  releaseId: string;
  configurationRevision: string;
  operationId: string;
  expectedRevision: number;
  runtimeVersion?: string;
  action?: "activate" | "rollback";
}

export interface StartParams {
  workflowId: string;
  idempotencyKey: string;
  input?: JsonObject;
  source?: "manual" | "api";
}

export interface ReleaseClient {
  publish(
    projectId: string,
    sourceRevision: number,
    idempotencyKey: string,
  ): Promise<ProjectRelease>;
  getActivation(projectId: string): Promise<ProjectActivation>;
  activate(
    projectId: string,
    params: ActivateParams,
  ): Promise<ProjectActivation>;
  start(projectId: string, params: StartParams): Promise<ExecutionAccepted>;
  getExecution(
    projectId: string,
    executionId: string,
  ): Promise<ExecutionStatus>;
}

const MAX_BYTES = 6 * 1024 * 1024;
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

function parseRelease(value: unknown, projectId: string): ProjectRelease {
  if (
    !isObject(value) ||
    value.project_id !== projectId ||
    !isIdentifier(value.release_id) ||
    !positive(value.source_revision) ||
    !isDigest(value.checksum)
  )
    throw new Error();
  return {
    project_id: projectId,
    release_id: value.release_id,
    source_revision: value.source_revision,
    checksum: value.checksum,
  };
}

function parseActivation(value: unknown): ProjectActivation {
  if (!isObject(value) || !nonNegative(value.revision)) throw new Error();
  if (value.release_id !== null && !isIdentifier(value.release_id))
    throw new Error();
  if (value.revision === 0) {
    return { revision: 0, release_id: null };
  }
  if (
    !isIdentifier(value.release_id) ||
    !isIdentifier(value.project_id) ||
    !isIdentifier(value.operation_id) ||
    !(
      value.previous_release_id === null ||
      isIdentifier(value.previous_release_id)
    ) ||
    !isIdentifier(value.runtime_version) ||
    !isIdentifier(value.configuration_revision) ||
    !isDigest(value.checksum) ||
    typeof value.actor_id !== "string" ||
    value.actor_id.length < 1 ||
    value.actor_id.length > 128 ||
    typeof value.created_at !== "string" ||
    value.created_at.length > 64 ||
    !TIMESTAMP.test(value.created_at)
  )
    throw new Error();
  return {
    revision: value.revision,
    release_id: value.release_id,
    project_id: value.project_id,
    operation_id: value.operation_id,
    previous_release_id: value.previous_release_id,
    runtime_version: value.runtime_version,
    configuration_revision: value.configuration_revision,
    checksum: value.checksum,
    actor_id: value.actor_id,
    created_at: value.created_at,
  };
}

function parseAccepted(value: unknown): ExecutionAccepted {
  if (
    !isObject(value) ||
    !isIdentifier(value.execution_id) ||
    !isIdentifier(value.release_id) ||
    !isIdentifier(value.configuration_revision) ||
    value.state !== "accepted"
  )
    throw new Error();
  return {
    execution_id: value.execution_id,
    release_id: value.release_id,
    configuration_revision: value.configuration_revision,
    state: "accepted",
  };
}

function parseStatus(value: unknown): ExecutionStatus {
  if (
    !isObject(value) ||
    !isIdentifier(value.release_id) ||
    !isDigest(value.definition_digest) ||
    !isIdentifier(value.runtime_version) ||
    typeof value.node_id !== "string" ||
    value.node_id.length > 128 ||
    typeof value.state !== "string" ||
    value.state.length < 1 ||
    value.state.length > 64 ||
    !nonNegative(value.revision) ||
    !Array.isArray(value.completed_nodes) ||
    value.completed_nodes.length > 200 ||
    !value.completed_nodes.every(
      (item) => typeof item === "string" && item.length <= 128,
    )
  )
    throw new Error();
  return {
    release_id: value.release_id,
    definition_digest: value.definition_digest,
    runtime_version: value.runtime_version,
    node_id: value.node_id,
    state: value.state,
    revision: value.revision,
    completed_nodes: (value.completed_nodes as string[]).slice(),
  };
}

export function createReleaseClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): ReleaseClient {
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
    method: "GET" | "POST",
    body?: string,
    ok: ReadonlySet<number> = new Set([200]),
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
        if (!ok.has(response.status)) {
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
    async publish(projectId, sourceRevision, idempotencyKey) {
      if (
        !isIdentifier(projectId) ||
        !positive(sourceRevision) ||
        !isIdentifier(idempotencyKey)
      )
        throw new ProjectApiError("invalid_request");
      const value = await request(
        `/${projectId}/releases`,
        "POST",
        JSON.stringify({
          source_revision: sourceRevision,
          idempotency_key: idempotencyKey,
        }),
        new Set([201]),
      );
      try {
        const release = parseRelease(value, projectId);
        if (release.source_revision !== sourceRevision) throw new Error();
        return release;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getActivation(projectId) {
      if (!isIdentifier(projectId)) throw new ProjectApiError("invalid_request");
      const value = await request(`/${projectId}/activation`, "GET");
      try {
        return parseActivation(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async activate(projectId, params) {
      const runtimeVersion = params.runtimeVersion ?? "proof.v1";
      const action = params.action ?? "activate";
      if (
        !isIdentifier(projectId) ||
        !isIdentifier(params.releaseId) ||
        !isIdentifier(params.configurationRevision) ||
        !isIdentifier(params.operationId) ||
        !nonNegative(params.expectedRevision) ||
        !Number.isSafeInteger(params.expectedRevision + 1) ||
        !isIdentifier(runtimeVersion) ||
        (action !== "activate" && action !== "rollback")
      )
        throw new ProjectApiError("invalid_request");
      const value = await request(
        `/${projectId}/activation`,
        "POST",
        JSON.stringify({
          project_id: projectId,
          release_id: params.releaseId,
          configuration_revision: params.configurationRevision,
          operation_id: params.operationId,
          expected_revision: params.expectedRevision,
          runtime_version: runtimeVersion,
          action,
        }),
      );
      try {
        const activation = parseActivation(value);
        if (
          activation.release_id !== params.releaseId ||
          activation.project_id !== projectId ||
          activation.operation_id !== params.operationId ||
          activation.configuration_revision !== params.configurationRevision
        )
          throw new Error();
        return activation;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async start(projectId, params) {
      if (
        !isIdentifier(projectId) ||
        !isIdentifier(params.workflowId) ||
        !isIdentifier(params.idempotencyKey) ||
        (params.source !== undefined &&
          params.source !== "manual" &&
          params.source !== "api") ||
        (params.input !== undefined && !isObject(params.input))
      )
        throw new ProjectApiError("invalid_request");
      const value = await request(
        `/${projectId}/executions`,
        "POST",
        JSON.stringify({
          workflow_id: params.workflowId,
          idempotency_key: params.idempotencyKey,
          input: params.input ?? {},
          source: params.source ?? "manual",
        }),
        new Set([202]),
      );
      try {
        return parseAccepted(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getExecution(projectId, executionId) {
      if (!isIdentifier(projectId) || !isIdentifier(executionId))
        throw new ProjectApiError("invalid_request");
      const value = await request(
        `/${projectId}/executions/${executionId}`,
        "GET",
      );
      try {
        return parseStatus(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}

/** Stable keys for retries after ambiguous network failures. */
export function newIdempotencyKey(prefix: string): string {
  const id =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `k-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  const key = `${prefix}-${id}`;
  if (!isIdentifier(key)) throw new ProjectApiError("invalid_request");
  return key;
}

export const TERMINAL_EXECUTION_STATES = new Set([
  "completed",
  "failed",
  "cancelled",
  "canceled",
]);
