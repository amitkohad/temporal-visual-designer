import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectApiError } from "./project-api";
import {
  createTemplateClient,
  publishBlockedByValidation,
} from "./template-api";
import { templateList, templateProjectRevision } from "../tests/project-fixtures";

const connection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "unit-private-token",
};
const ROOT = "/api/v1/organizations/acme/environments/dev";

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("template API client", () => {
  it("lists MVP templates and creates an independent Dev draft", async () => {
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      if (path === `${ROOT}/project-templates`) return response(templateList());
      if (path === `${ROOT}/project-templates/employee-onboarding/projects`) {
        expect(init?.method).toBe("POST");
        const body = JSON.parse(String(init?.body));
        expect(body).toEqual({
          project_id: "people",
          name: "People onboarding",
          assignments: {
            manager: {
              rule: "any_one",
              user_ids: ["manager-user"],
              group_ids: [],
            },
            hr: { rule: "any_one", user_ids: ["manager-user"], group_ids: [] },
            it: { rule: "any_one", user_ids: ["manager-user"], group_ids: [] },
          },
        });
        return response(templateProjectRevision(), 201);
      }
      throw new Error(path);
    });
    const client = createTemplateClient(connection, fetcher);
    const listed = await client.listTemplates();
    expect(listed.map((item) => item.template_id)).toEqual([
      "employee-onboarding",
      "purchase-requisition",
    ]);
    const created = await client.createProject("employee-onboarding", {
      project_id: "people",
      name: "People onboarding",
      assignments: {
        manager: { user_ids: ["manager-user"] },
        hr: { user_ids: ["manager-user"] },
        it: { user_ids: ["manager-user"] },
      },
    });
    expect(created.revision).toBe(1);
    expect(created.project_id).toBe("people");
    expect(created.content.workflows).toHaveLength(1);
  });

  it("validates project content and exposes the publish gate helper", async () => {
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      expect(String(url)).toBe(`${ROOT}/projects/people/validation`);
      expect(init?.method).toBe("POST");
      return response([
        {
          path: "/workflows/0/nodes/1",
          code: "unreachable_node",
          message: "Node is unreachable from a declared start",
        },
      ]);
    });
    const client = createTemplateClient(connection, fetcher);
    const issues = await client.validateProject("people", {
      schema_version: "0.1",
      workflows: [],
    });
    expect(issues).toHaveLength(1);
    expect(publishBlockedByValidation(issues)).toBe(true);
    expect(publishBlockedByValidation([])).toBe(false);
  });

  it("maps HTTP failures to private codes without echoing bodies", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      response({ secret: "unit-private-token" }, 409),
    );
    const client = createTemplateClient(connection, fetcher);
    await expect(
      client.createProject("employee-onboarding", {
        project_id: "people",
        name: "People onboarding",
        assignments: {
          manager: { user_ids: ["manager-user"] },
          hr: { user_ids: ["manager-user"] },
          it: { user_ids: ["manager-user"] },
        },
      }),
    ).rejects.toMatchObject({ code: "conflict" } satisfies Partial<ProjectApiError>);
  });
});
