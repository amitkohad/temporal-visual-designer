import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Tasks from "../src/Tasks";

const TOKEN = "acceptance-private-token-do-not-persist";
const BASE = "/api/v1/organizations/local-demo/environments/dev/tasks";

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function task(overrides: Record<string, unknown> = {}) {
  return {
    task_id: "task-1",
    scope: { organization_id: "local-demo", environment_id: "dev" },
    request: {
      run_id: "run-42",
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
  };
}

function form(revision = 1) {
  return {
    revision,
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
        field_id: "notes",
        label: "Notes",
        schema_json: '{"type":"string"}',
        editable: true,
        required: false,
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
}

function server() {
  let revision = 1;
  let submitted = false;
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    const path = String(url);
    const method = init?.method ?? "GET";
    if (path === `${BASE}?status=pending&limit=50&offset=0&run_id=run-42`) {
      return response({
        items: submitted ? [] : [task({ revision })],
        next_offset: null,
      });
    }
    if (path === `${BASE}?status=pending&limit=50&offset=0`) {
      return response({
        items: submitted ? [] : [task({ revision })],
        next_offset: null,
      });
    }
    if (path === `${BASE}/task-1` && method === "GET") {
      return response(
        task({ revision, status: submitted ? "approved" : "pending" }),
      );
    }
    if (path === `${BASE}/task-1/form` && method === "GET") {
      return response(form(revision));
    }
    if (path === `${BASE}/task-1/form` && method === "POST") {
      const body = JSON.parse(String(init?.body));
      expect(body.expected_revision).toBe(revision);
      expect(body.values.confirmed).toBe(true);
      revision += 1;
      return response({ revision });
    }
    if (path === `${BASE}/task-1/decisions` && method === "POST") {
      const body = JSON.parse(String(init?.body));
      expect(body.expected_revision).toBe(revision);
      expect(body.decision).toBe("approve");
      expect(typeof body.command_id).toBe("string");
      expect(body.command_id.length).toBeGreaterThan(0);
      if (!submitted && body.decision === "approve" && revision === 1) {
        return response({ code: "conflict" }, 409);
      }
      submitted = true;
      revision += 1;
      return response(task({ revision, status: "approved" }));
    }
    return response({ code: "not_found" }, 404);
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}

async function connect(runId = "run-42") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Organization ID"), "local-demo");
  const environment = screen.getByLabelText("Environment ID");
  await user.clear(environment);
  await user.type(environment, "dev");
  await user.type(screen.getByLabelText("Access token"), TOKEN);
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await screen.findByRole("button", { name: "Open task task-1" });
  if (runId) {
    const filter = screen.getByLabelText("Filter by run ID");
    await user.clear(filter);
    await user.type(filter, runId);
    await user.click(screen.getByRole("button", { name: "Refresh inbox" }));
    await screen.findByRole("button", { name: "Open task task-1" });
  }
  return user;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("task portal through mocked HTTP", () => {
  it("keeps bearer credentials in memory through form submit and approve", async () => {
    const fetcher = server();
    const local = vi.spyOn(Storage.prototype, "setItem");
    render(<Tasks onClose={vi.fn()} />);
    const user = await connect();
    expect(screen.queryByLabelText("Access token")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(location.href).not.toContain(TOKEN);
    await user.click(screen.getByRole("button", { name: "Open task task-1" }));
    await screen.findByRole("heading", { name: "manager" });
    expect(screen.getByText("Ada")).toBeInTheDocument();
    await user.click(screen.getByLabelText(/Confirm request/));
    await user.type(screen.getByLabelText("Notes"), "Reviewed");
    await user.click(screen.getByRole("button", { name: "Submit form" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([path, init]) =>
            String(path).endsWith("/task-1/form") && init?.method === "POST",
        ),
      ).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(
          ([path, init]) =>
            String(path).endsWith("/task-1/decisions") &&
            init?.method === "POST",
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Open task task-1" }),
      ).not.toBeInTheDocument(),
    );
    expect(local).not.toHaveBeenCalled();
    expect(
      fetcher.mock.calls.every(([path]) => !String(path).includes(TOKEN)),
    ).toBe(true);
    const decision = fetcher.mock.calls.find(
      ([path, init]) =>
        String(path).endsWith("/decisions") && init?.method === "POST",
    );
    const headers = new Headers(decision?.[1]?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    expect(screen.getByText(/Task approved/i)).toBeInTheDocument();
  });

  it("rejects without requiring form submission", async () => {
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      if (path.includes("?") && method === "GET") {
        return response({ items: [task()], next_offset: null });
      }
      if (path.endsWith("/task-1") && method === "GET") {
        return response(task());
      }
      if (path.endsWith("/form") && method === "GET") {
        return response({ code: "not_found" }, 404);
      }
      if (path.endsWith("/decisions") && method === "POST") {
        const body = JSON.parse(String(init?.body));
        expect(body).toMatchObject({
          expected_revision: 1,
          decision: "reject",
        });
        return response(task({ status: "rejected", revision: 2 }));
      }
      return response({ code: "not_found" }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    render(<Tasks />);
    const user = await connect("");
    await user.click(screen.getByRole("button", { name: "Open task task-1" }));
    await screen.findByText("No managed form is bound to this task.");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.some(([path]) =>
          String(path).endsWith("/decisions"),
        ),
      ).toBe(true),
    );
    expect(screen.getByText(/Task rejected/i)).toBeInTheDocument();
  });

  it("surfaces approve conflicts when the form gate blocks the decision", async () => {
    const fetcher = server();
    render(<Tasks />);
    const user = await connect();
    await user.click(screen.getByRole("button", { name: "Open task task-1" }));
    await screen.findByRole("button", { name: "Approve" });
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /blocked until the managed form is submitted/i,
    );
    expect(
      within(screen.getByLabelText("Task detail")).getByText("pending"),
    ).toBeInTheDocument();
    expect(
      fetcher.mock.calls.some(([path]) => String(path).endsWith("/decisions")),
    ).toBe(true);
  });
});
