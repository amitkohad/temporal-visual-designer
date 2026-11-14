/** Private, bounded same-origin task API. Tokens never leave request headers. */
import { isIdentifier, isObject } from "./project-api";
import type { JsonObject, JsonValue, ProjectConnection } from "./project-api";

export type TaskConnection = ProjectConnection;
export type TaskStatus = "pending" | "approved" | "rejected";
export type TaskDecision = "approve" | "reject";
export type TaskErrorCode =
  | "invalid_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "unavailable"
  | "invalid_response";

export class TaskApiError extends Error {
  readonly code: TaskErrorCode;
  constructor(code: TaskErrorCode) {
    super("Task request failed");
    this.name = "TaskApiError";
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

export interface TaskView {
  task_id: string;
  status: TaskStatus;
  revision: number;
  created_at: string;
  run_id: string;
  node_invocation_id: string;
  definition_revision: string;
  rule: string;
  eligible_approver_ids: string[];
  stage_index: number;
}

export interface TaskPage {
  items: TaskView[];
  next_offset: number | null;
}

export interface TaskFormField {
  field_id: string;
  label: string;
  schema_json: string;
  editable: boolean;
  required: boolean;
  write_only: boolean;
}

export interface TaskFormView {
  revision: number;
  fields: TaskFormField[];
  values: JsonObject;
}

export interface TaskListQuery {
  status?: TaskStatus;
  runId?: string;
  limit?: number;
  offset?: number;
}

export interface TaskClient {
  listTasks(query?: TaskListQuery): Promise<TaskPage>;
  getTask(taskId: string): Promise<TaskView>;
  getForm(taskId: string): Promise<TaskFormView | null>;
  submitForm(
    taskId: string,
    expectedRevision: number,
    values: JsonObject,
  ): Promise<number>;
  decide(
    taskId: string,
    commandId: string,
    expectedRevision: number,
    decision: TaskDecision,
  ): Promise<TaskView>;
}

const MAX_BYTES = 6 * 1024 * 1024;
const encoder = new TextEncoder();

function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}

function nonNegative(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function timestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 64 &&
    Number.isFinite(Date.parse(value)) &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(
      value,
    ) &&
    new Date(value).toISOString().slice(0, 19) === value.slice(0, 19)
  );
}

function status(value: unknown): value is TaskStatus {
  return value === "pending" || value === "approved" || value === "rejected";
}

function identifiers(value: unknown): value is string[] {
  return (
    Array.isArray(value) &&
    value.length >= 1 &&
    value.length <= 1000 &&
    value.every(isIdentifier)
  );
}

function taskView(
  value: unknown,
  organizationId: string,
  environmentId: string,
): TaskView {
  if (
    !isObject(value) ||
    !isIdentifier(value.task_id) ||
    !status(value.status) ||
    !positive(value.revision) ||
    !timestamp(value.created_at) ||
    !isObject(value.scope) ||
    value.scope.organization_id !== organizationId ||
    value.scope.environment_id !== environmentId ||
    !isObject(value.request) ||
    !isIdentifier(value.request.run_id) ||
    !isIdentifier(value.request.node_invocation_id) ||
    !isIdentifier(value.request.definition_revision) ||
    typeof value.request.rule !== "string" ||
    value.request.rule.length < 1 ||
    value.request.rule.length > 64 ||
    !identifiers(value.eligible_approver_ids) ||
    !nonNegative(value.stage_index) ||
    value.stage_index > 19
  )
    throw new Error();
  return {
    task_id: value.task_id,
    status: value.status,
    revision: value.revision,
    created_at: value.created_at,
    run_id: value.request.run_id,
    node_invocation_id: value.request.node_invocation_id,
    definition_revision: value.request.definition_revision,
    rule: value.request.rule,
    eligible_approver_ids: [...value.eligible_approver_ids],
    stage_index: value.stage_index,
  };
}

function formField(value: unknown): TaskFormField {
  if (
    !isObject(value) ||
    !isIdentifier(value.field_id) ||
    typeof value.label !== "string" ||
    value.label.length < 1 ||
    value.label.length > 200 ||
    typeof value.schema_json !== "string" ||
    value.schema_json.length < 2 ||
    value.schema_json.length > 65536 ||
    typeof value.editable !== "boolean" ||
    typeof value.required !== "boolean" ||
    typeof value.write_only !== "boolean"
  )
    throw new Error();
  JSON.parse(value.schema_json);
  return {
    field_id: value.field_id,
    label: value.label,
    schema_json: value.schema_json,
    editable: value.editable,
    required: value.required,
    write_only: value.write_only,
  };
}

function formView(value: unknown): TaskFormView {
  if (
    !isObject(value) ||
    !positive(value.revision) ||
    !Array.isArray(value.fields) ||
    value.fields.length > 200 ||
    typeof value.values_json !== "string" ||
    value.values_json.length > MAX_BYTES
  )
    throw new Error();
  const parsed = JSON.parse(value.values_json) as unknown;
  if (!isObject(parsed)) throw new Error();
  const ids = new Set<string>();
  const fields = value.fields.map((field) => {
    const item = formField(field);
    if (ids.has(item.field_id)) throw new Error();
    ids.add(item.field_id);
    return item;
  });
  return { revision: value.revision, fields, values: { ...parsed } };
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

export function createTaskClient(
  connection: TaskConnection,
  fetcher: typeof fetch = fetch,
): TaskClient {
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
    throw new TaskApiError("invalid_request");
  const organizationId = connection.organizationId;
  const environmentId = connection.environmentId;
  const token = connection.token;
  const base = `/api/v1/organizations/${organizationId}/environments/${environmentId}/tasks`;

  async function request(
    path: string,
    method: "GET" | "POST" = "GET",
    body?: string,
    allowNotFound = false,
  ): Promise<unknown | null> {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const deadline = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new TaskApiError("unavailable"));
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
        if (controller.signal.aborted) throw new TaskApiError("unavailable");
        if (allowNotFound && response.status === 404) {
          void response.body?.cancel().catch(() => undefined);
          return null;
        }
        if (!response.ok) {
          const code: TaskErrorCode =
            (
              {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                409: "conflict",
                422: "invalid_request",
              } as Record<number, TaskErrorCode>
            )[response.status] ?? "unavailable";
          void response.body?.cancel().catch(() => undefined);
          throw new TaskApiError(code);
        }
        try {
          return await boundedJson(response, controller.signal);
        } catch {
          throw new TaskApiError("invalid_response");
        }
      })();
      return await Promise.race([operation, deadline]);
    } catch (error) {
      throw error instanceof TaskApiError
        ? error
        : new TaskApiError("unavailable");
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  return {
    async listTasks(query = {}) {
      const statusFilter = query.status ?? "pending";
      const limit = query.limit ?? 50;
      const offset = query.offset ?? 0;
      if (
        !status(statusFilter) ||
        !positive(limit) ||
        limit > 100 ||
        !nonNegative(offset) ||
        offset > 10000 ||
        (query.runId !== undefined && !isIdentifier(query.runId))
      )
        throw new TaskApiError("invalid_request");
      const params = new URLSearchParams();
      params.set("status", statusFilter);
      params.set("limit", String(limit));
      params.set("offset", String(offset));
      if (query.runId !== undefined) params.set("run_id", query.runId);
      const value = await request("?" + params.toString());
      try {
        if (
          !isObject(value) ||
          !Array.isArray(value.items) ||
          value.items.length > 100 ||
          !(value.next_offset === null || nonNegative(value.next_offset)) ||
          (typeof value.next_offset === "number" && value.next_offset > 10000)
        )
          throw new Error();
        const ids = new Set<string>();
        const items = value.items.map((item) => {
          const task = taskView(item, organizationId, environmentId);
          if (ids.has(task.task_id)) throw new Error();
          ids.add(task.task_id);
          return task;
        });
        return { items, next_offset: value.next_offset };
      } catch {
        throw new TaskApiError("invalid_response");
      }
    },
    async getTask(taskId) {
      if (!isIdentifier(taskId)) throw new TaskApiError("invalid_request");
      const value = await request("/" + taskId);
      try {
        return taskView(value, organizationId, environmentId);
      } catch {
        throw new TaskApiError("invalid_response");
      }
    },
    async getForm(taskId) {
      if (!isIdentifier(taskId)) throw new TaskApiError("invalid_request");
      const value = await request(
        "/" + taskId + "/form",
        "GET",
        undefined,
        true,
      );
      if (value === null) return null;
      try {
        return formView(value);
      } catch {
        throw new TaskApiError("invalid_response");
      }
    },
    async submitForm(taskId, expectedRevision, values) {
      let body: string;
      try {
        if (!isIdentifier(taskId) || !positive(expectedRevision))
          throw new Error();
        const saved = snapshot(values);
        body = canonical({
          expected_revision: expectedRevision,
          values: saved,
        });
        if (encoder.encode(body).byteLength > MAX_BYTES) throw new Error();
      } catch {
        throw new TaskApiError("invalid_request");
      }
      const value = await request("/" + taskId + "/form", "POST", body);
      try {
        if (!isObject(value) || !positive(value.revision)) throw new Error();
        if (value.revision !== expectedRevision + 1) throw new Error();
        return value.revision;
      } catch {
        throw new TaskApiError("invalid_response");
      }
    },
    async decide(taskId, commandId, expectedRevision, decision) {
      let body: string;
      try {
        if (
          !isIdentifier(taskId) ||
          !isIdentifier(commandId) ||
          !positive(expectedRevision) ||
          (decision !== "approve" && decision !== "reject")
        )
          throw new Error();
        body = canonical({
          command_id: commandId,
          expected_revision: expectedRevision,
          decision,
        });
      } catch {
        throw new TaskApiError("invalid_request");
      }
      const value = await request("/" + taskId + "/decisions", "POST", body);
      try {
        const task = taskView(value, organizationId, environmentId);
        if (task.task_id !== taskId) throw new Error();
        if (decision === "approve" && task.status !== "approved") {
          if (task.status !== "pending") throw new Error();
        }
        if (decision === "reject" && task.status !== "rejected")
          throw new Error();
        return task;
      } catch {
        throw new TaskApiError("invalid_response");
      }
    },
  };
}

export type { JsonObject, JsonValue };
