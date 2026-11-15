import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import DebuggerPanel from "../src/DebuggerPanel";
import type { DebuggerClient } from "../src/debugger-api";
import type { ProjectClient } from "../src/project-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const connection = {
  organizationId: "local-demo",
  environmentId: "dev",
  token: "ui-private-token",
};

const session = {
  session_id: "sess-1",
  project_id: "welcome",
  source_revision: 1,
  workflow_id: "welcome",
  configuration_revision: "local-v1",
  definition_digest: digest,
  actor_id: "alice",
  created_at: "2026-11-15T00:00:00Z",
  execution_id: "exec-1",
  temporal_run_id: "run-1",
  state: "started" as const,
};

const snapshot = {
  release_id: "rel-1",
  definition_digest: digest,
  runtime_version: "proof.v1",
  node_id: "wait",
  state: "paused",
  revision: 0,
  completed_nodes: ["start"],
};

function mockDebugger(overrides: Partial<DebuggerClient> = {}): DebuggerClient {
  return {
    listSessions: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    startSession: vi.fn().mockResolvedValue(session),
    getStatus: vi.fn().mockResolvedValue({ session, snapshot }),
    reconnect: vi.fn().mockResolvedValue({
      session_id: "sess-1",
      snapshot,
      breakpoints: ["wait"],
      earliest_sequence: 0,
      latest_sequence: 1,
      next_after: 1,
    }),
    listEvents: vi.fn().mockResolvedValue({
      session_id: "sess-1",
      events: [
        {
          sequence: 1,
          kind: "paused",
          node_id: "wait",
          state: "paused",
          revision: 0,
        },
      ],
      earliest_sequence: 1,
      latest_sequence: 1,
      next_after: 1,
      has_more: false,
      gap: false,
    }),
    sendCommand: vi.fn().mockResolvedValue({ command_id: "cmd-1", revision: 1 }),
    getBreakpoints: vi.fn().mockResolvedValue({
      session_id: "sess-1",
      revision: 0,
      breakpoints: ["wait"],
    }),
    replaceBreakpoints: vi
      .fn()
      .mockResolvedValue({ command_id: "bp-1", revision: 0 }),
    inspectInputs: vi.fn().mockResolvedValue({
      session_id: "sess-1",
      workflow_id: "welcome",
      source_revision: 1,
      definition_digest: digest,
      values: { public: "visible" },
      redacted_paths: ["private"],
      truncated: false,
    }),
    inspectStepInput: vi.fn(),
    inspectStepOutput: vi.fn(),
    rerun: vi.fn(),
    ...overrides,
  };
}

function mockProjects(): ProjectClient {
  return {
    listProjects: vi.fn().mockResolvedValue([
      { project_id: "welcome", name: "Welcome", head_revision: 1 },
    ]),
    readProject: vi.fn(),
    saveProject: vi.fn(),
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DebuggerPanel critical path", () => {
  it("connects, starts a session, steps, and inspects workflow inputs", async () => {
    const client = mockDebugger();
    const projects = mockProjects();
    const user = userEvent.setup();
    render(
      <DebuggerPanel
        onClose={() => undefined}
        connection={connection}
        initialProjectId="welcome"
        initialWorkflowId="welcome"
        initialSourceRevision={1}
        client={client}
        projectClient={projects}
        pollIntervalMs={60_000}
      />,
    );

    expect(await screen.findByRole("heading", { name: "Debugger" })).toBeVisible();
    await waitFor(() => expect(projects.listProjects).toHaveBeenCalled());
    expect(
      screen.getByText(/Host needs enable-debugger/),
    ).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Breakpoints (comma-separated node IDs)"));
    await user.type(
      screen.getByLabelText("Breakpoints (comma-separated node IDs)"),
      "wait",
    );
    await user.click(
      screen.getByRole("button", { name: "Start debug session" }),
    );

    await waitFor(() =>
      expect(client.startSession).toHaveBeenCalledWith(
        "welcome",
        expect.objectContaining({
          workflow_id: "welcome",
          source_revision: 1,
          mode: "real",
          breakpoints: ["wait"],
        }),
      ),
    );
    expect(
      await screen.findByText(/Started debug session sess-1/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Runtime/)).toBeInTheDocument();
    expect(screen.getByText("paused", { selector: "strong" })).toBeInTheDocument();

    const controls = screen.getByLabelText("Runtime controls");
    await user.click(within(controls).getByRole("button", { name: "Step over" }));
    await waitFor(() =>
      expect(client.sendCommand).toHaveBeenCalledWith(
        "welcome",
        "sess-1",
        expect.objectContaining({
          action: "step_over",
          node_id: "wait",
          expected_revision: 0,
        }),
      ),
    );

    await user.click(
      screen.getByRole("button", { name: "Workflow inputs" }),
    );
    await waitFor(() => expect(client.inspectInputs).toHaveBeenCalled());
    expect(await screen.findByText(/"public": "visible"/)).toBeInTheDocument();
    expect(screen.getByText(/Redacted paths: private/)).toBeInTheDocument();
  });

  it("surfaces forbidden when the debugger provider is denied", async () => {
    const { ProjectApiError } = await import("../src/project-api");
    const client = mockDebugger({
      startSession: vi
        .fn()
        .mockRejectedValue(new ProjectApiError("forbidden")),
    });
    const user = userEvent.setup();
    render(
      <DebuggerPanel
        onClose={() => undefined}
        connection={connection}
        initialProjectId="welcome"
        client={client}
        projectClient={mockProjects()}
        pollIntervalMs={60_000}
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Start debug session" })).toBeEnabled(),
    );
    await user.click(
      screen.getByRole("button", { name: "Start debug session" }),
    );
    expect(
      await screen.findByRole("alert"),
    ).toHaveTextContent(/Dev-only for the session owner/);
  });
});
