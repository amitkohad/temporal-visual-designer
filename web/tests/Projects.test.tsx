import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Projects from "../src/Projects";
import App from "../src/App";
import {
  projectList,
  projectRevision,
  saveAcknowledgement,
  templateList,
  templateProjectRevision,
} from "./project-fixtures";

const TOKEN = "acceptance-private-token-do-not-persist";
const ROOT = "/api/v1/organizations/acme/environments/dev";
const BASE = `${ROOT}/projects`;
function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}
function server() {
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    const path = String(url);
    if (path.includes("/project-templates/") && path.endsWith("/projects"))
      return response(
        templateProjectRevision(
          JSON.parse(String(init?.body)).project_id,
          JSON.parse(String(init?.body)).name,
        ),
        201,
      );
    if (path.includes("/project-templates")) return response(templateList());
    if (path.includes("/validation")) return response([]);
    if (path.includes("?")) return response(projectList());
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      const projectId = path.slice(path.lastIndexOf("/") + 1);
      return response({
        ...saveAcknowledgement(body),
        project_id: projectId,
      });
    }
    return response(projectRevision());
  });
  vi.stubGlobal("fetch", fetcher);
  return fetcher;
}
async function connect() {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Organization ID"), "acme");
  const environment = screen.getByLabelText("Environment ID");
  await user.clear(environment);
  await user.type(environment, "dev");
  await user.type(screen.getByLabelText("Access token"), TOKEN);
  await user.click(screen.getByRole("button", { name: "Connect" }));
  await screen.findByRole("button", { name: "Open hr" });
  return user;
}
async function open() {
  const user = await connect();
  await user.click(screen.getByRole("button", { name: "Open hr" }));
  await screen.findAllByRole("button", {
    name: "Select Manager review",
  });
  await user.click(
    screen.getAllByRole("button", {
      name: "Select Manager review",
    })[0],
  );
  return user;
}
async function edit(label = "Finance review") {
  const user = userEvent.setup();
  await user.clear(screen.getByRole("textbox", { name: "Step name" }));
  await user.type(screen.getByRole("textbox", { name: "Step name" }), label);
  await user.click(screen.getByRole("button", { name: "Apply changes" }));
}
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("connected project layouts through mocked HTTP", () => {
  it("keeps bearer credentials in memory and preserves full project content on acknowledged save", async () => {
    const fetcher = server();
    const local = vi.spyOn(Storage.prototype, "setItem");
    render(<Projects onClose={vi.fn()} />);
    await open();
    await edit();
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
      ).toHaveLength(1),
    );
    const [url, init] = fetcher.mock.calls.find(
      ([, options]) => options?.method === "PUT",
    )!;
    const body = JSON.parse(String(init?.body));
    expect(url).toBe(`${BASE}/hr`);
    expect(body.expected_revision).toBe(7);
    expect(body.summary).toBe("Update designer layout");
    const original = projectRevision().content;
    for (const key of [
      "workflows",
      "forms",
      "activity_refs",
      "connections",
      "configuration_schema",
      "tests",
    ])
      expect(body.content[key]).toEqual(original[key]);
    expect(
      body.content.layout.nexus_flow_designer.workflows.onboarding.nodes.review
        .label,
    ).toBe("Finance review");
    expect(
      body.content.layout.nexus_flow_designer.workflows.onboarding.nodes.start
        .extension,
    ).toBe("keep node extension");
    expect(body.content.layout.nexus_flow_designer.workflows.purchase).toEqual(
      (original.layout as any).nexus_flow_designer.workflows.purchase,
    );
    expect(local).not.toHaveBeenCalled();
    expect(location.href).not.toContain(TOKEN);
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(
      fetcher.mock.calls.every(([path]) => !String(path).includes(TOKEN)),
    ).toBe(true);
    const headers = new Headers(init?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);
    await screen.findByText("Saved project revision 8.");
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save project" }),
      ).toBeDisabled(),
    );
  });

  it("preserves local edits on conflict and reloads only after explicit discard", async () => {
    const fetcher = server();
    render(<Projects onClose={vi.fn()} />);
    await open();
    await edit();
    fetcher.mockImplementationOnce(async () =>
      response({ message: TOKEN }, 409),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await screen.findByText(/conflict|another.*revision|changed.*since/i);
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Finance review",
    );
    expect(document.body.textContent).not.toContain(TOKEN);
    const calls = fetcher.mock.calls.length;
    await userEvent.click(
      screen.getByRole("button", { name: "Reload latest" }),
    );
    await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    expect(fetcher).toHaveBeenCalledTimes(calls);
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Finance review",
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Reload latest" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Discard changes" }),
    );
    await screen.findAllByRole("button", {
      name: "Select Manager review",
    });
    expect(fetcher).toHaveBeenCalledTimes(calls + 1);
  });

  it("retains edits and expected revision for an explicit retry after 503", async () => {
    const fetcher = server();
    render(<Projects onClose={vi.fn()} />);
    await open();
    await edit();
    fetcher.mockImplementationOnce(async () =>
      response({ message: TOKEN }, 503),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save project" }),
      ).toBeEnabled(),
    );
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Finance review",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
      ).toHaveLength(2),
    );
    const bodies = fetcher.mock.calls
      .filter(([, init]) => init?.method === "PUT")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies[0]).toEqual(bodies[1]);
    expect(bodies[1].expected_revision).toBe(7);
  });

  it("guards pending property edits and close without silently discarding", async () => {
    server();
    const close = vi.fn();
    render(<Projects onClose={close} />);
    await open();
    await userEvent.clear(screen.getByRole("textbox", { name: "Step name" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Step name" }),
      "Unapplied draft",
    );
    expect(screen.getByRole("button", { name: "Save project" })).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Close projects" }),
    );
    expect(close).not.toHaveBeenCalled();
    await screen.findByRole("alertdialog", {
      name: "Discard unsaved changes?",
    });
    await userEvent.click(screen.getByRole("button", { name: "Keep editing" }));
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Unapplied draft",
    );
    const unload = new Event("beforeunload", { cancelable: true });
    fireEvent(window, unload);
    expect(unload.defaultPrevented).toBe(true);
    await userEvent.click(
      screen.getByRole("button", { name: "Close projects" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Discard changes" }),
    );
    expect(close).toHaveBeenCalledTimes(1);
  });

  for (const malformed of ["kind", "metadata"])
    it(`rejects unsupported ${malformed} without a partial editable view`, async () => {
      const fetcher = server();
      render(<Projects onClose={vi.fn()} />);
      const user = await connect();
      const revision = projectRevision();
      if (malformed === "kind")
        (revision.content.workflows as any[])[0].nodes[1].kind = "activity";
      else
        (
          revision.content.layout as any
        ).nexus_flow_designer.workflows.onboarding.nodes.review.x = "400";
      fetcher.mockImplementationOnce(async () => response(revision));
      await user.click(screen.getByRole("button", { name: "Open hr" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        /unsupported|cannot.*shown|cannot.*display|cannot.*open/i,
      );
      expect(
        screen.queryByRole("textbox", { name: "Step name" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save project" }),
      ).not.toBeInTheDocument();
    });

  it("disables session and navigation while an open request is pending", async () => {
    const fetcher = server();
    render(<Projects onClose={vi.fn()} />);
    const user = await connect();
    let finish!: (response: Response) => void;
    fetcher.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await user.click(screen.getByRole("button", { name: "Open hr" }));
    expect(screen.getByRole("button", { name: "Disconnect" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Close projects" }),
    ).toBeDisabled();
    expect(screen.getByRole("button", { name: "Open hr" })).toBeDisabled();
    finish(response(projectRevision()));
    await screen.findAllByRole("button", {
      name: "Select Manager review",
    });
  });

  it("keeps acknowledged layouts across workflow switches and later saves", async () => {
    const fetcher = server();
    render(<Projects onClose={vi.fn()} />);
    await open();
    await edit("Finance review");
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Workflow" }),
      "purchase",
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Select Purchase review" })[0],
    );
    await edit("Procurement review");
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Save project" }),
      ).toBeDisabled(),
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Workflow" }),
      "onboarding",
    );
    await userEvent.click(
      screen.getAllByRole("button", { name: "Select Finance review" })[0],
    );
    await edit("Final HR review");
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
      ).toHaveLength(2),
    );
    const body = JSON.parse(
      String(
        fetcher.mock.calls.filter(([, init]) => init?.method === "PUT")[1][1]
          ?.body,
      ),
    );
    expect(body.expected_revision).toBe(8);
    expect(
      body.content.layout.nexus_flow_designer.workflows.purchase.nodes.review
        .label,
    ).toBe("Procurement review");
    expect(
      body.content.layout.nexus_flow_designer.workflows.onboarding.nodes.review
        .label,
    ).toBe("Final HR review");
  }, 15000);

  it("rejects a mismatched save acknowledgement and retains the dirty revision", async () => {
    const fetcher = server();
    render(<Projects onClose={vi.fn()} />);
    await open();
    await edit();
    fetcher.mockImplementationOnce(async () => response(projectRevision(8)));
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await screen.findByText(/unsupported response/i);
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Finance review",
    );
    expect(screen.getByRole("button", { name: "Save project" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
      ).toHaveLength(2),
    );
    expect(
      JSON.parse(String(fetcher.mock.calls.at(-1)?.[1]?.body))
        .expected_revision,
    ).toBe(7);
  });

  it("ignores an old mounted session response after a new session is opened", async () => {
    const fetcher = server();
    const oldView = render(<Projects onClose={vi.fn()} />);
    const user = await connect();
    let finish!: (response: Response) => void;
    fetcher.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await user.click(screen.getByRole("button", { name: "Open hr" }));
    oldView.unmount();
    render(<Projects onClose={vi.fn()} />);
    await open();
    await edit("New session review");
    await act(async () => {
      finish(response(projectRevision(9)));
    });
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "New session review",
    );
    expect(
      screen.queryByText("Loaded project revision 9."),
    ).not.toBeInTheDocument();
  });

  for (const reserved of ["__proto__", "constructor"])
    it(`supports opaque workflow and node identifiers ${reserved} without inherited state`, async () => {
      const fetcher = server();
      render(<Projects onClose={vi.fn()} />);
      const user = await connect();
      const revision = projectRevision();
      const workflow = (revision.content.workflows as any[])[1];
      workflow.workflow_id = reserved;
      workflow.nodes[1].node_id = "constructor";
      workflow.edges[0].target_node_id = "constructor";
      workflow.edges[1].source_node_id = "constructor";
      const metadata = (revision.content.layout as any).nexus_flow_designer
        .workflows;
      const prior = metadata.purchase;
      delete metadata.purchase;
      Object.defineProperty(metadata, reserved, {
        enumerable: true,
        configurable: true,
        writable: true,
        value: {
          extension: "retain opaque workflow",
          nodes: {
            constructor: {
              ...prior.nodes.review,
              extension: "retain opaque node",
            },
          },
        },
      });
      fetcher.mockImplementationOnce(async () => response(revision));
      await user.click(screen.getByRole("button", { name: "Open hr" }));
      await screen.findByRole("combobox", { name: "Workflow" });
      await user.selectOptions(
        screen.getByRole("combobox", { name: "Workflow" }),
        reserved,
      );
      await user.click(
        screen.getAllByRole("button", { name: "Select Purchase review" })[0],
      );
      expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
        "Purchase review",
      );
      await edit("Opaque review");
      await user.click(screen.getByRole("button", { name: "Save project" }));
      await waitFor(() =>
        expect(
          fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
        ).toHaveLength(1),
      );
      const saved = JSON.parse(
        String(
          fetcher.mock.calls.find(([, init]) => init?.method === "PUT")?.[1]
            ?.body,
        ),
      );
      const persisted =
        saved.content.layout.nexus_flow_designer.workflows[reserved];
      expect(persisted.extension).toBe("retain opaque workflow");
      expect(persisted.nodes.constructor.label).toBe("Opaque review");
      expect(persisted.nodes.constructor.extension).toBe("retain opaque node");
      expect(saved.content.workflows).toEqual(revision.content.workflows);
    });
});

describe("executable designer critical path", () => {
  it("creates a template draft, inserts a wait step, and saves executable content", async () => {
    const fetcher = server();
    render(<Projects onClose={vi.fn()} />);
    const user = await connect();
    await screen.findByRole("heading", { name: "Create from template" });
    await user.clear(screen.getByLabelText("Project ID"));
    await user.type(screen.getByLabelText("Project ID"), "people");
    await user.clear(screen.getByLabelText("Project name"));
    await user.type(screen.getByLabelText("Project name"), "People onboarding");
    for (const slot of ["manager", "hr", "it"]) {
      await user.type(
        screen.getByLabelText(`${slot} assignee user ID`),
        "manager-user",
      );
    }
    await user.click(screen.getByRole("button", { name: "Create draft" }));
    await screen.findByText(/Created Employee onboarding draft at revision 1/);
    await user.click(
      screen.getAllByRole("button", { name: "Select manager" })[0],
    );
    await user.click(screen.getByRole("button", { name: "Insert wait" }));
    await screen.findByText(/Wait step inserted locally/);
    await user.click(screen.getByRole("button", { name: "Save project" }));
    await waitFor(() =>
      expect(
        fetcher.mock.calls.filter(([, init]) => init?.method === "PUT"),
      ).toHaveLength(1),
    );
    const [, init] = fetcher.mock.calls.find(
      ([, options]) => options?.method === "PUT",
    )!;
    const body = JSON.parse(String(init?.body));
    expect(body.expected_revision).toBe(1);
    expect(body.summary).toBe("Update workflow steps");
    const nodes = body.content.workflows[0].nodes;
    expect(nodes.some((node: { kind: string }) => node.kind === "wait")).toBe(
      true,
    );
    expect(
      nodes.find((node: { node_id: string }) => node.node_id === "manager")
        .input_mapping.human_task.user_ids,
    ).toEqual(["manager-user"]);
    await screen.findByText("Saved project revision 2.");
  });

  it("surfaces validation issues and reports publish blocked for 1C", async () => {
    const fetcher = server();
    const gates: { publishBlocked: boolean; validationIssues: unknown[] }[] =
      [];
    render(
      <Projects
        onClose={vi.fn()}
        onPublishGateChange={(gate) => {
          gates.push({
            publishBlocked: gate.publishBlocked,
            validationIssues: gate.validationIssues,
          });
        }}
      />,
    );
    await open();
    fetcher.mockImplementationOnce(async () =>
      response([
        {
          path: "/workflows/0/nodes/1",
          code: "unreachable_node",
          message: "Node is unreachable from a declared start",
        },
      ]),
    );
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));
    await screen.findByRole("status", { name: "Validation issues" });
    expect(screen.getByText(/Publish is blocked/)).toBeVisible();
    expect(
      gates.some(
        (gate) => gate.publishBlocked && gate.validationIssues.length === 1,
      ),
    ).toBe(true);
  });
});

describe("sample to connected project navigation", () => {
  it("opens a clean sample and returns through the projects close action", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Projects" }));
    expect(screen.getByLabelText("Organization ID")).toBeVisible();
    expect(
      screen.queryByRole("alertdialog", { name: "Leave sample?" }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Close projects" }),
    );
    expect(
      screen.getByText("Sample project · changes stay in this tab"),
    ).toBeVisible();
  });

  it("preserves pending sample edits until navigation is explicitly confirmed", async () => {
    render(<App />);
    await userEvent.clear(screen.getByRole("textbox", { name: "Step name" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Step name" }),
      "Pending sample review",
    );
    await userEvent.click(screen.getByRole("button", { name: "Projects" }));
    await screen.findByRole("alertdialog", { name: "Leave sample?" });
    expect(screen.queryByLabelText("Organization ID")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: "Keep sample editing" }),
    );
    expect(screen.getByRole("textbox", { name: "Step name" })).toHaveValue(
      "Pending sample review",
    );
    await userEvent.click(screen.getByRole("button", { name: "Projects" }));
    await userEvent.click(
      screen.getByRole("button", { name: "Discard sample changes" }),
    );
    expect(screen.getByLabelText("Organization ID")).toBeVisible();
  });
});
