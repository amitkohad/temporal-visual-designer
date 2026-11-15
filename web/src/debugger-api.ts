/**
 * Dev debugger client against existing signed debug-session APIs.
 * Same privacy posture as project-api (token in Authorization only).
 */
import {
  ProjectApiError,
  isIdentifier,
  isObject,
  type JsonObject,
  type JsonValue,
  type ProjectConnection,
  type ProjectErrorCode,
} from "./project-api";

export type { ProjectConnection, ProjectErrorCode, JsonObject, JsonValue };
export { ProjectApiError as DebuggerApiError };

export type DebugSessionState = "dispatch_pending" | "started";
export type DebugControlAction = "step_over" | "continue" | "cancel";
export type DebugMode = "real";

export interface DebugSession {
  session_id: string;
  project_id: string;
  source_revision: number;
  workflow_id: string;
  configuration_revision: string;
  definition_digest: string;
  actor_id: string;
  created_at: string;
  execution_id: string;
  temporal_run_id: string | null;
  state: DebugSessionState;
}

export interface DebugSnapshot {
  release_id: string;
  definition_digest: string;
  runtime_version: string;
  node_id: string;
  state: string;
  revision: number;
  completed_nodes: string[];
}

export interface DebugStatus {
  session: DebugSession;
  snapshot: DebugSnapshot;
}

export interface DebugStartRequest {
  operation_id: string;
  source_revision: number;
  workflow_id: string;
  configuration_revision: string;
  input?: JsonObject;
  breakpoints?: string[];
  mode: DebugMode;
}

export interface DebugControlRequest {
  command_id: string;
  node_id: string;
  action: DebugControlAction;
  expected_revision: number;
}

export interface DebugBreakpointReplaceRequest {
  command_id: string;
  node_id: string;
  breakpoints: string[];
  expected_revision: number;
}

export interface CommandReceipt {
  command_id: string;
  revision: number;
}

export interface DebugSessionPage {
  items: DebugSession[];
  next_cursor: string | null;
}

export interface DebugReconnect {
  session_id: string;
  snapshot: DebugSnapshot;
  breakpoints: string[];
  earliest_sequence: number;
  latest_sequence: number;
  next_after: number;
}

export interface DebugBreakpoints {
  session_id: string;
  revision: number;
  breakpoints: string[];
}

export interface DebugEvent {
  sequence: number;
  kind: string;
  node_id: string | null;
  state: string;
  revision: number;
}

export interface DebugEvents {
  session_id: string;
  events: DebugEvent[];
  earliest_sequence: number;
  latest_sequence: number;
  next_after: number;
  has_more: boolean;
  gap: boolean;
}

export interface ActivityRef {
  activity_id: string;
  version: string;
  artifact_digest: string;
}

export interface DebugInputInspection {
  session_id: string;
  workflow_id: string;
  source_revision: number;
  definition_digest: string;
  values: JsonObject;
  redacted_paths: string[];
  truncated: boolean;
}

export interface DebugStepInspection {
  session_id: string;
  workflow_id: string;
  source_revision: number;
  definition_digest: string;
  node_id: string;
  activity_ref: ActivityRef;
  values: JsonObject;
  redacted_paths: string[];
  truncated: boolean;
}

export interface DebuggerClient {
  listSessions(
    projectId: string,
    limit?: number,
    cursor?: string | null,
  ): Promise<DebugSessionPage>;
  startSession(
    projectId: string,
    request: DebugStartRequest,
  ): Promise<DebugSession>;
  getStatus(projectId: string, sessionId: string): Promise<DebugStatus>;
  reconnect(projectId: string, sessionId: string): Promise<DebugReconnect>;
  listEvents(
    projectId: string,
    sessionId: string,
    after?: number,
    limit?: number,
  ): Promise<DebugEvents>;
  sendCommand(
    projectId: string,
    sessionId: string,
    request: DebugControlRequest,
  ): Promise<CommandReceipt>;
  getBreakpoints(
    projectId: string,
    sessionId: string,
  ): Promise<DebugBreakpoints>;
  replaceBreakpoints(
    projectId: string,
    sessionId: string,
    request: DebugBreakpointReplaceRequest,
  ): Promise<CommandReceipt>;
  inspectInputs(
    projectId: string,
    sessionId: string,
  ): Promise<DebugInputInspection>;
  inspectStepInput(
    projectId: string,
    sessionId: string,
    nodeId: string,
  ): Promise<DebugStepInspection>;
  inspectStepOutput(
    projectId: string,
    sessionId: string,
    nodeId: string,
  ): Promise<DebugStepInspection>;
  rerun(
    projectId: string,
    sessionId: string,
    request: DebugStartRequest,
  ): Promise<DebugSession>;
}

const MAX_BYTES = 6 * 1024 * 1024;
const DIGEST = /^sha256:[a-f0-9]{64}$/;
const TIMESTAMP =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+$/;

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

function isStringList(value: unknown, max = 100): value is string[] {
  return (
    Array.isArray(value) &&
    value.length <= max &&
    value.every((item) => isIdentifier(item))
  );
}

export function parseSnapshot(value: unknown): DebugSnapshot {
  if (
    !isObject(value) ||
    !isIdentifier(value.release_id) ||
    !isDigest(value.definition_digest) ||
    typeof value.runtime_version !== "string" ||
    value.runtime_version.length < 1 ||
    value.runtime_version.length > 128 ||
    !isIdentifier(value.node_id) ||
    typeof value.state !== "string" ||
    value.state.length < 1 ||
    value.state.length > 64 ||
    !nonNegative(value.revision) ||
    !isStringList(value.completed_nodes, 1000)
  ) {
    throw new Error();
  }
  return {
    release_id: value.release_id,
    definition_digest: value.definition_digest,
    runtime_version: value.runtime_version,
    node_id: value.node_id,
    state: value.state,
    revision: value.revision,
    completed_nodes: [...value.completed_nodes],
  };
}

export function parseDebugSession(
  value: unknown,
  projectId: string,
): DebugSession {
  if (
    !isObject(value) ||
    !isIdentifier(value.session_id) ||
    value.project_id !== projectId ||
    !positive(value.source_revision) ||
    !isIdentifier(value.workflow_id) ||
    !isIdentifier(value.configuration_revision) ||
    !isDigest(value.definition_digest) ||
    typeof value.actor_id !== "string" ||
    value.actor_id.length < 1 ||
    value.actor_id.length > 256 ||
    !isTimestamp(value.created_at) ||
    !isIdentifier(value.execution_id) ||
    !(
      value.temporal_run_id === null ||
      (typeof value.temporal_run_id === "string" &&
        value.temporal_run_id.length >= 1 &&
        value.temporal_run_id.length <= 256)
    ) ||
    (value.state !== "dispatch_pending" && value.state !== "started")
  ) {
    throw new Error();
  }
  return {
    session_id: value.session_id,
    project_id: projectId,
    source_revision: value.source_revision,
    workflow_id: value.workflow_id,
    configuration_revision: value.configuration_revision,
    definition_digest: value.definition_digest,
    actor_id: value.actor_id,
    created_at: value.created_at,
    execution_id: value.execution_id,
    temporal_run_id: value.temporal_run_id,
    state: value.state,
  };
}

function parseReceipt(value: unknown): CommandReceipt {
  if (
    !isObject(value) ||
    !isIdentifier(value.command_id) ||
    !nonNegative(value.revision)
  ) {
    throw new Error();
  }
  return { command_id: value.command_id, revision: value.revision };
}

function parseActivityRef(value: unknown): ActivityRef {
  if (
    !isObject(value) ||
    !isIdentifier(value.activity_id) ||
    typeof value.version !== "string" ||
    !VERSION.test(value.version) ||
    !isDigest(value.artifact_digest)
  ) {
    throw new Error();
  }
  return {
    activity_id: value.activity_id,
    version: value.version,
    artifact_digest: value.artifact_digest,
  };
}

function parseInspectionBase(value: unknown): {
  session_id: string;
  workflow_id: string;
  source_revision: number;
  definition_digest: string;
  values: JsonObject;
  redacted_paths: string[];
  truncated: boolean;
} {
  if (
    !isObject(value) ||
    !isIdentifier(value.session_id) ||
    !isIdentifier(value.workflow_id) ||
    !positive(value.source_revision) ||
    !isDigest(value.definition_digest) ||
    !isObject(value.values) ||
    !Array.isArray(value.redacted_paths) ||
    value.redacted_paths.length > 100 ||
    !value.redacted_paths.every((path) => typeof path === "string") ||
    typeof value.truncated !== "boolean"
  ) {
    throw new Error();
  }
  return {
    session_id: value.session_id,
    workflow_id: value.workflow_id,
    source_revision: value.source_revision,
    definition_digest: value.definition_digest,
    values: value.values,
    redacted_paths: value.redacted_paths.map(String),
    truncated: value.truncated,
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

export function newDebuggerOperationId(prefix: string): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Parse comma/whitespace breakpoint text into sorted unique identifiers. */
export function parseBreakpointText(text: string): string[] {
  const parts = text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  const unique = [...new Set(parts)];
  if (unique.length > 100 || unique.some((part) => !isIdentifier(part))) {
    throw new ProjectApiError("invalid_request");
  }
  return unique.sort();
}

export function createDebuggerClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): DebuggerClient {
  if (
    !connection ||
    !isIdentifier(connection.organizationId) ||
    !isIdentifier(connection.environmentId) ||
    typeof connection.token !== "string" ||
    connection.token.length < 1 ||
    connection.token.length > 16384 ||
    !/^[A-Za-z0-9._~+\/-]+=*$/.test(connection.token) ||
    typeof fetcher !== "function"
  ) {
    throw new ProjectApiError("invalid_request");
  }
  const organizationId = connection.organizationId;
  const environmentId = connection.environmentId;
  const token = connection.token;
  const base = `/api/v1/organizations/${organizationId}/environments/${environmentId}/projects`;

  async function request(
    path: string,
    method: "GET" | "POST" = "GET",
    body?: string,
  ): Promise<unknown> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new ProjectApiError("unavailable"));
      }, 15000);
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

  function sessionsPath(projectId: string, suffix = ""): string {
    if (!isIdentifier(projectId)) throw new ProjectApiError("invalid_request");
    return `/${projectId}/debug-sessions${suffix}`;
  }

  return {
    async listSessions(projectId, limit = 50, cursor = null) {
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
        throw new ProjectApiError("invalid_request");
      }
      const params = new URLSearchParams({ limit: String(limit) });
      if (cursor) {
        if (typeof cursor !== "string" || cursor.length > 2048) {
          throw new ProjectApiError("invalid_request");
        }
        params.set("cursor", cursor);
      }
      const value = await request(
        `${sessionsPath(projectId)}?${params.toString()}`,
      );
      try {
        if (
          !isObject(value) ||
          !Array.isArray(value.items) ||
          value.items.length > 100 ||
          !(value.next_cursor === null || typeof value.next_cursor === "string")
        ) {
          throw new Error();
        }
        return {
          items: value.items.map((item) => parseDebugSession(item, projectId)),
          next_cursor: value.next_cursor,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async startSession(projectId, start) {
      if (
        !isIdentifier(start.operation_id) ||
        !positive(start.source_revision) ||
        !isIdentifier(start.workflow_id) ||
        !isIdentifier(start.configuration_revision) ||
        start.mode !== "real" ||
        (start.breakpoints !== undefined &&
          !isStringList(start.breakpoints, 100))
      ) {
        throw new ProjectApiError("invalid_request");
      }
      const payload = {
        operation_id: start.operation_id,
        source_revision: start.source_revision,
        workflow_id: start.workflow_id,
        configuration_revision: start.configuration_revision,
        input: start.input ?? {},
        breakpoints: start.breakpoints ?? [],
        mode: "real" as const,
      };
      const value = await request(
        sessionsPath(projectId),
        "POST",
        JSON.stringify(payload),
      );
      try {
        return parseDebugSession(value, projectId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getStatus(projectId, sessionId) {
      if (!isIdentifier(sessionId)) throw new ProjectApiError("invalid_request");
      const value = await request(sessionsPath(projectId, `/${sessionId}`));
      try {
        if (!isObject(value)) throw new Error();
        return {
          session: parseDebugSession(value.session, projectId),
          snapshot: parseSnapshot(value.snapshot),
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async reconnect(projectId, sessionId) {
      if (!isIdentifier(sessionId)) throw new ProjectApiError("invalid_request");
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/reconnect`),
      );
      try {
        if (
          !isObject(value) ||
          value.session_id !== sessionId ||
          !isStringList(value.breakpoints, 100) ||
          !nonNegative(value.earliest_sequence) ||
          !nonNegative(value.latest_sequence) ||
          !nonNegative(value.next_after)
        ) {
          throw new Error();
        }
        return {
          session_id: sessionId,
          snapshot: parseSnapshot(value.snapshot),
          breakpoints: [...value.breakpoints],
          earliest_sequence: value.earliest_sequence,
          latest_sequence: value.latest_sequence,
          next_after: value.next_after,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async listEvents(projectId, sessionId, after = 0, limit = 25) {
      if (
        !isIdentifier(sessionId) ||
        !nonNegative(after) ||
        !Number.isSafeInteger(limit) ||
        limit < 1 ||
        limit > 25
      ) {
        throw new ProjectApiError("invalid_request");
      }
      const params = new URLSearchParams({
        after: String(after),
        limit: String(limit),
      });
      const value = await request(
        `${sessionsPath(projectId, `/${sessionId}/events`)}?${params}`,
      );
      try {
        if (
          !isObject(value) ||
          value.session_id !== sessionId ||
          !Array.isArray(value.events) ||
          value.events.length > 25 ||
          !nonNegative(value.earliest_sequence) ||
          !nonNegative(value.latest_sequence) ||
          !nonNegative(value.next_after) ||
          typeof value.has_more !== "boolean" ||
          typeof value.gap !== "boolean"
        ) {
          throw new Error();
        }
        const events = value.events.map((event) => {
          if (
            !isObject(event) ||
            !positive(event.sequence) ||
            typeof event.kind !== "string" ||
            !(event.node_id === null || isIdentifier(event.node_id)) ||
            typeof event.state !== "string" ||
            !nonNegative(event.revision)
          ) {
            throw new Error();
          }
          return {
            sequence: event.sequence,
            kind: event.kind,
            node_id: event.node_id,
            state: event.state,
            revision: event.revision,
          };
        });
        return {
          session_id: sessionId,
          events,
          earliest_sequence: value.earliest_sequence,
          latest_sequence: value.latest_sequence,
          next_after: value.next_after,
          has_more: value.has_more,
          gap: value.gap,
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async sendCommand(projectId, sessionId, control) {
      if (
        !isIdentifier(sessionId) ||
        !isIdentifier(control.command_id) ||
        !isIdentifier(control.node_id) ||
        (control.action !== "step_over" &&
          control.action !== "continue" &&
          control.action !== "cancel") ||
        !nonNegative(control.expected_revision)
      ) {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/commands`),
        "POST",
        JSON.stringify(control),
      );
      try {
        return parseReceipt(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async getBreakpoints(projectId, sessionId) {
      if (!isIdentifier(sessionId)) throw new ProjectApiError("invalid_request");
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/breakpoints`),
      );
      try {
        if (
          !isObject(value) ||
          value.session_id !== sessionId ||
          !nonNegative(value.revision) ||
          !isStringList(value.breakpoints, 100)
        ) {
          throw new Error();
        }
        return {
          session_id: sessionId,
          revision: value.revision,
          breakpoints: [...value.breakpoints],
        };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async replaceBreakpoints(projectId, sessionId, control) {
      if (
        !isIdentifier(sessionId) ||
        !isIdentifier(control.command_id) ||
        !isIdentifier(control.node_id) ||
        !isStringList(control.breakpoints, 100) ||
        !nonNegative(control.expected_revision)
      ) {
        throw new ProjectApiError("invalid_request");
      }
      const unique = [...new Set(control.breakpoints)].sort();
      if (unique.length !== control.breakpoints.length) {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/breakpoints`),
        "POST",
        JSON.stringify({ ...control, breakpoints: unique }),
      );
      try {
        return parseReceipt(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async inspectInputs(projectId, sessionId) {
      if (!isIdentifier(sessionId)) throw new ProjectApiError("invalid_request");
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/inputs`),
      );
      try {
        return parseInspectionBase(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async inspectStepInput(projectId, sessionId, nodeId) {
      if (!isIdentifier(sessionId) || !isIdentifier(nodeId)) {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/steps/${nodeId}/input`),
      );
      try {
        if (!isObject(value) || value.node_id !== nodeId) throw new Error();
        const base = parseInspectionBase(value);
        return { ...base, node_id: nodeId, activity_ref: parseActivityRef(value.activity_ref) };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async inspectStepOutput(projectId, sessionId, nodeId) {
      if (!isIdentifier(sessionId) || !isIdentifier(nodeId)) {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/steps/${nodeId}/output`),
      );
      try {
        if (!isObject(value) || value.node_id !== nodeId) throw new Error();
        const base = parseInspectionBase(value);
        return { ...base, node_id: nodeId, activity_ref: parseActivityRef(value.activity_ref) };
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },

    async rerun(projectId, sessionId, start) {
      if (!isIdentifier(sessionId)) throw new ProjectApiError("invalid_request");
      if (
        !isIdentifier(start.operation_id) ||
        !positive(start.source_revision) ||
        !isIdentifier(start.workflow_id) ||
        !isIdentifier(start.configuration_revision) ||
        start.mode !== "real"
      ) {
        throw new ProjectApiError("invalid_request");
      }
      const payload = {
        operation_id: start.operation_id,
        source_revision: start.source_revision,
        workflow_id: start.workflow_id,
        configuration_revision: start.configuration_revision,
        input: start.input ?? {},
        breakpoints: start.breakpoints ?? [],
        mode: "real" as const,
      };
      const value = await request(
        sessionsPath(projectId, `/${sessionId}/reruns`),
        "POST",
        JSON.stringify(payload),
      );
      try {
        return parseDebugSession(value, projectId);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}
