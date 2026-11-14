/** Same-origin template discovery, draft creation, and authoring validation. */
import {
  isIdentifier,
  isObject,
  ProjectApiError,
  type JsonObject,
  type JsonValue,
  type ProjectConnection,
  type ProjectErrorCode,
  type ProjectRevision,
} from "./project-api";

export type ValidationIssue = {
  path: string;
  code: string;
  message: string;
};

export type TemplateAssignment = {
  rule?: "any_one" | "all_approvers";
  user_ids?: string[];
  group_ids?: string[];
};

export type TemplateInfo = {
  template_id: string;
  version: "1";
  name: string;
  description: string;
  workflow_id: string;
  assignment_slots: string[];
  input_schema: JsonObject;
};

export type CreateTemplateProjectRequest = {
  project_id: string;
  name: string;
  assignments: Record<string, TemplateAssignment>;
};

export interface TemplateClient {
  listTemplates(): Promise<TemplateInfo[]>;
  getTemplate(id: string): Promise<TemplateInfo>;
  createProject(
    templateId: string,
    request: CreateTemplateProjectRequest,
  ): Promise<ProjectRevision>;
  validateProject(
    projectId: string,
    content: JsonObject,
  ): Promise<ValidationIssue[]>;
}

const MAX_BYTES = 6 * 1024 * 1024;
const encoder = new TextEncoder();
const MVP_TEMPLATES = new Set(["employee-onboarding", "purchase-requisition"]);

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

function parseTemplate(value: unknown): TemplateInfo {
  if (
    !isObject(value) ||
    !isIdentifier(value.template_id) ||
    !MVP_TEMPLATES.has(value.template_id) ||
    value.version !== "1" ||
    typeof value.name !== "string" ||
    value.name.length < 1 ||
    value.name.length > 200 ||
    typeof value.description !== "string" ||
    value.description.length > 2000 ||
    !isIdentifier(value.workflow_id) ||
    !Array.isArray(value.assignment_slots) ||
    value.assignment_slots.length < 1 ||
    value.assignment_slots.length > 20 ||
    !value.assignment_slots.every(isIdentifier) ||
    new Set(value.assignment_slots).size !== value.assignment_slots.length ||
    !isObject(value.input_schema)
  )
    throw new Error();
  return {
    template_id: value.template_id,
    version: "1",
    name: value.name,
    description: value.description,
    workflow_id: value.workflow_id,
    assignment_slots: value.assignment_slots as string[],
    input_schema: snapshot(value.input_schema),
  };
}

function parseIssues(value: unknown): ValidationIssue[] {
  if (!Array.isArray(value) || value.length > 1000) throw new Error();
  return value.map((item) => {
    if (
      !isObject(item) ||
      typeof item.path !== "string" ||
      item.path.length > 1000 ||
      typeof item.code !== "string" ||
      item.code.length < 1 ||
      item.code.length > 128 ||
      typeof item.message !== "string" ||
      item.message.length < 1 ||
      item.message.length > 1000
    )
      throw new Error();
    return {
      path: item.path,
      code: item.code,
      message: item.message,
    };
  });
}

function encodeAssignment(value: TemplateAssignment): JsonObject {
  const rule = value.rule ?? "any_one";
  if (rule !== "any_one" && rule !== "all_approvers") throw new Error();
  const userIds = value.user_ids ?? [];
  const groupIds = value.group_ids ?? [];
  if (
    !Array.isArray(userIds) ||
    !Array.isArray(groupIds) ||
    userIds.length > 1000 ||
    groupIds.length > 1000 ||
    !userIds.every(isIdentifier) ||
    !groupIds.every(isIdentifier) ||
    new Set(userIds).size !== userIds.length ||
    new Set(groupIds).size !== groupIds.length ||
    (userIds.length === 0 && groupIds.length === 0)
  )
    throw new Error();
  return {
    rule,
    user_ids: userIds,
    group_ids: groupIds,
  };
}

/** True when validation reported located authoring issues — 1C should disable publish. */
export function publishBlockedByValidation(
  issues: readonly ValidationIssue[],
): boolean {
  return Array.isArray(issues) && issues.length > 0;
}

export function createTemplateClient(
  connection: ProjectConnection,
  fetcher: typeof fetch = fetch,
): TemplateClient {
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
    method: "GET" | "POST" = "GET",
    body?: string,
    okStatuses: number[] = [200],
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
        if (!okStatuses.includes(response.status)) {
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
    async listTemplates() {
      const value = await request("/project-templates");
      try {
        if (!Array.isArray(value) || value.length > 20) throw new Error();
        const ids = new Set<string>();
        const templates = value.map((item) => {
          const template = parseTemplate(item);
          if (ids.has(template.template_id)) throw new Error();
          ids.add(template.template_id);
          return template;
        });
        if (![...MVP_TEMPLATES].every((id) => ids.has(id))) throw new Error();
        return templates;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async getTemplate(id) {
      if (!isIdentifier(id) || !MVP_TEMPLATES.has(id))
        throw new ProjectApiError("invalid_request");
      const value = await request("/project-templates/" + id);
      try {
        const template = parseTemplate(value);
        if (template.template_id !== id) throw new Error();
        return template;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async createProject(templateId, requestBody) {
      let body: string;
      let projectId: string;
      try {
        if (!isIdentifier(templateId) || !MVP_TEMPLATES.has(templateId))
          throw new Error();
        if (
          !requestBody ||
          !isIdentifier(requestBody.project_id) ||
          typeof requestBody.name !== "string" ||
          !requestBody.name.trim() ||
          requestBody.name.length > 200 ||
          !isObject(requestBody.assignments as unknown as JsonValue) ||
          Object.keys(requestBody.assignments).length < 1 ||
          Object.keys(requestBody.assignments).length > 20
        )
          throw new Error();
        projectId = requestBody.project_id;
        const assignments: JsonObject = {};
        for (const [slot, assignment] of Object.entries(
          requestBody.assignments,
        )) {
          if (!isIdentifier(slot)) throw new Error();
          assignments[slot] = encodeAssignment(assignment);
        }
        body = canonical({
          project_id: projectId,
          name: requestBody.name,
          assignments,
        });
        if (encoder.encode(body).byteLength > MAX_BYTES) throw new Error();
      } catch {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request(
        "/project-templates/" + templateId + "/projects",
        "POST",
        body,
        [201],
      );
      try {
        const created = revision(value, projectId);
        if (created.revision !== 1 || created.parent_revision !== null)
          throw new Error();
        return created;
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
    async validateProject(projectId, content) {
      let body: string;
      try {
        if (!isIdentifier(projectId)) throw new Error();
        const saved = snapshot(content);
        if (saved.schema_version !== "0.1" || !Array.isArray(saved.workflows))
          throw new Error();
        body = canonical({ content: saved });
        if (encoder.encode(body).byteLength > MAX_BYTES) throw new Error();
      } catch {
        throw new ProjectApiError("invalid_request");
      }
      const value = await request(
        "/projects/" + projectId + "/validation",
        "POST",
        body,
      );
      try {
        return parseIssues(value);
      } catch {
        throw new ProjectApiError("invalid_response");
      }
    },
  };
}
