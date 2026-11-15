import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import FormBuilder from "../src/FormBuilder";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const content = {
  schema_version: "0.1",
  workflows: [
    {
      workflow_id: "main",
      schema_version: "0.1",
      input_schema: { type: "object", properties: {}, additionalProperties: false },
      output_schema: {},
      start_definitions: [{ start_id: "manual", node_id: "start", kind: "manual" }],
      nodes: [
        {
          node_id: "start",
          kind: "start",
          named_ports: { inputs: [], outputs: ["out"] },
          input_mapping: {},
        },
        {
          node_id: "manager",
          kind: "approval",
          named_ports: { inputs: ["in"], outputs: ["out"] },
          input_mapping: {
            human_task: {
              rule: "any_one",
              user_ids: ["u1"],
              group_ids: [],
            },
          },
        },
        {
          node_id: "end",
          kind: "end",
          named_ports: { inputs: ["in"], outputs: [] },
          input_mapping: {},
        },
      ],
      edges: [
        {
          source_node_id: "start",
          source_port: "out",
          target_node_id: "manager",
          target_port: "in",
        },
        {
          source_node_id: "manager",
          source_port: "out",
          target_node_id: "end",
          target_port: "in",
        },
      ],
      error_routes: [],
    },
  ],
  forms: [],
};

function revision(number = 1, nextContent = content) {
  return {
    project_id: "demo",
    revision: number,
    parent_revision: number === 1 ? null : number - 1,
    summary: "draft",
    author_id: "local",
    created_at: "2026-11-15T00:00:00Z",
    content: nextContent,
  };
}

describe("FormBuilder", () => {
  it("authors a form, previews it, and saves a bound approval definition", async () => {
    let savedBody: unknown = null;
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      if (path.includes("/projects?") && method === "GET") {
        return response([
          {
            project_id: "demo",
            name: "Demo",
            head_revision: 1,
            scope: {
              organization_id: "local-demo",
              environment_id: "dev",
            },
          },
        ]);
      }
      if (path.endsWith("/projects/demo") && method === "GET") {
        return response(revision(1));
      }
      if (path.endsWith("/projects/demo") && method === "PUT") {
        savedBody = JSON.parse(String(init?.body));
        const next = (savedBody as { content: typeof content }).content;
        return response(revision(2, next));
      }
      return response({ code: "not_found" }, 404);
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup();
    render(<FormBuilder onClose={() => undefined} />);
    await user.clear(screen.getByLabelText("Organization ID"));
    await user.type(screen.getByLabelText("Organization ID"), "local-demo");
    await user.clear(screen.getByLabelText("Environment ID"));
    await user.type(screen.getByLabelText("Environment ID"), "dev");
    await user.type(screen.getByLabelText("Access token"), "test-token");
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await screen.findByRole("heading", { name: "Builder" });
    await user.click(screen.getByRole("button", { name: "New form" }));
    const formId = screen.getByLabelText("Form ID");
    await user.clear(formId);
    await user.type(formId, "confirm-form");
    const fieldId = screen.getByLabelText("Field ID");
    await user.clear(fieldId);
    await user.type(fieldId, "confirmed");
    const label = screen.getByLabelText("Label");
    await user.clear(label);
    await user.type(label, "I confirm");
    await user.selectOptions(screen.getByLabelText("Type"), "boolean");
    await user.click(screen.getByLabelText("Required"));
    await waitFor(() => {
      expect(
        within(screen.getByLabelText("Form preview")).getByLabelText(
          /I confirm \(required\)/i,
        ),
      ).toBeVisible();
    });
    await user.click(
      screen.getByRole("button", { name: "Save and bind to approval" }),
    );
    await waitFor(() => expect(savedBody).not.toBeNull());
    const body = savedBody as {
      expected_revision: number;
      content: {
        forms: Array<{ form_id: string }>;
        workflows: Array<{
          nodes: Array<{
            node_id: string;
            input_mapping?: {
              human_task?: { form?: { definition?: { form_id: string } } };
            };
          }>;
        }>;
      };
    };
    expect(body.expected_revision).toBe(1);
    expect(body.content.forms.some((form) => form.form_id === "confirm-form")).toBe(
      true,
    );
    const manager = body.content.workflows[0]!.nodes.find(
      (node) => node.node_id === "manager",
    );
    expect(manager?.input_mapping?.human_task?.form?.definition?.form_id).toBe(
      "confirm-form",
    );
    expect(
      await screen.findByText(/Saved and bound confirm-form to manager/i),
    ).toBeVisible();
  });
});
