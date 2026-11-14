import { afterEach, describe, expect, it, vi } from "vitest";
import { createTaskClient, TaskApiError } from "./task-api";
import type { TaskConnection } from "./task-api";

const connection: TaskConnection = {
  organizationId: "local-demo",
  environmentId: "dev",
  token: "private.jwt.token",
};

const task = (overrides: Record<string, unknown> = {}) => ({
  task_id: "task-1",
  scope: { organization_id: "local-demo", environment_id: "dev" },
  request: {
    run_id: "run-1",
    node_invocation_id: "manager",
    definition_revision: "r1",
    rule: "any_one",
    user_ids: ["user-1"],
    group_ids: [],
  },
  eligible_approver_ids: ["user-1"],
  status: "pending",
  revision: 1,
  decisions: [],
  assignment_history: [],
  delegation_history: [],
  stage_assignments: [],
  stage_index: 0,
  created_at: "2026-11-14T09:00:00+00:00",
  ...overrides,
});

const form = {
  revision: 1,
  fields: [
    {
      field_id: "confirmed",
      label: "Confirm request",
      schema_json: '{"type":"boolean","const":true}',
      editable: true,
      required: true,
      write_only: false,
    },
    {
      field_id: "employee_name",
      label: "Employee name",
      schema_json: '{"type":"string"}',
      editable: false,
      required: false,
      write_only: false,
    },
  ],
  values_json: '{"employee_name":"Ada"}',
};

const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });

const fetcher = (value: unknown, status = 200) =>
  vi.fn<typeof globalThis.fetch>().mockResolvedValue(response(value, status));

afterEach(() => vi.useRealTimers());

describe("private same-origin task API", () => {
  it("lists pending tasks for a run with fixed private request policy", async () => {
    const original = { ...connection };
    const fetch = fetcher({ items: [task()], next_offset: null });
    const client = createTaskClient(original, fetch);
    original.token = "changed";
    const page = await client.listTasks({ status: "pending", runId: "run-1" });
    expect(page).toEqual({
      items: [
        {
          task_id: "task-1",
          status: "pending",
          revision: 1,
          created_at: "2026-11-14T09:00:00+00:00",
          run_id: "run-1",
          node_invocation_id: "manager",
          definition_revision: "r1",
          rule: "any_one",
          eligible_approver_ids: ["user-1"],
          stage_index: 0,
        },
      ],
      next_offset: null,
    });
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organizations/local-demo/environments/dev/tasks?status=pending&limit=50&offset=0&run_id=run-1",
    );
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      headers: { Authorization: "Bearer private.jwt.token" },
      redirect: "error",
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    expect(String(fetch.mock.calls[0][0])).not.toContain("private.jwt");
  });

  it("loads a bound form and submits values with expected_revision", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response(form))
      .mockResolvedValueOnce(response({ revision: 2 }));
    const client = createTaskClient(connection, fetch);
    expect(await client.getForm("task-1")).toEqual({
      revision: 1,
      fields: form.fields,
      values: { employee_name: "Ada" },
    });
    expect(await client.submitForm("task-1", 1, { confirmed: true })).toBe(2);
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organizations/local-demo/environments/dev/tasks/task-1/form",
    );
    expect(fetch.mock.calls[1][1]).toMatchObject({ method: "POST" });
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({
      expected_revision: 1,
      values: { confirmed: true },
    });
  });

  it("treats missing forms as unbound and posts decisions with command_id", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response({ code: "not_found" }, 404))
      .mockResolvedValueOnce(
        response(task({ status: "approved", revision: 2 })),
      );
    const client = createTaskClient(connection, fetch);
    expect(await client.getForm("task-1")).toBeNull();
    const decided = await client.decide("task-1", "approve-1", 1, "approve");
    expect(decided.status).toBe("approved");
    expect(JSON.parse(String(fetch.mock.calls[1][1]?.body))).toEqual({
      command_id: "approve-1",
      expected_revision: 1,
      decision: "approve",
    });
  });

  it("accepts stage-advancing approve responses that remain pending", async () => {
    const fetch = fetcher(task({ status: "pending", revision: 2 }));
    const decided = await createTaskClient(connection, fetch).decide(
      "task-1",
      "approve-stage",
      1,
      "approve",
    );
    expect(decided).toMatchObject({ status: "pending", revision: 2 });
  });

  it.each([401, 403, 404, 409, 422, 500, 503])(
    "returns closed private status %i",
    async (status) => {
      const fetch = fetcher({ message: connection.token }, status);
      const failure = await createTaskClient(connection, fetch)
        .getTask("task-1")
        .catch((error) => error);
      expect(failure).toBeInstanceOf(TaskApiError);
      expect(failure.message).not.toContain("private");
      expect(failure.code).toBe(
        (
          {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
          } as Record<number, string>
        )[status] ?? "unavailable",
      );
    },
  );

  it.each([
    { organizationId: "../other" },
    { environmentId: "." },
    { token: "" },
    { token: "private token" },
  ])("rejects malformed connection %# without requests", (changes) => {
    const fetch = fetcher({ items: [], next_offset: null });
    expect(() =>
      createTaskClient({ ...connection, ...changes }, fetch),
    ).toThrow(TaskApiError);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects foreign-scoped inbox payloads", async () => {
    await expect(
      createTaskClient(
        connection,
        fetcher({
          items: [
            task({
              scope: { organization_id: "other", environment_id: "dev" },
            }),
          ],
          next_offset: null,
        }),
      ).listTasks(),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
});
