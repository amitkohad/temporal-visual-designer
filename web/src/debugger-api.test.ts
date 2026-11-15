import { describe, expect, it, vi, afterEach } from "vitest";
import {
  createDebuggerClient,
  parseBreakpointText,
  parseDebugSession,
  parseSnapshot,
} from "./debugger-api";
import { ProjectApiError } from "./project-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const connection = {
  organizationId: "local-demo",
  environmentId: "dev",
  token: "ui-private-token",
};

const sessionBody = {
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
  state: "started",
};

const snapshotBody = {
  release_id: "rel-1",
  definition_digest: digest,
  runtime_version: "proof.v1",
  node_id: "wait",
  state: "paused",
  revision: 0,
  completed_nodes: ["start"],
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("debugger-api parsers", () => {
  it("parses snapshots and sessions", () => {
    expect(parseSnapshot(snapshotBody)).toEqual(snapshotBody);
    expect(parseDebugSession(sessionBody, "welcome")).toEqual(sessionBody);
  });

  it("rejects mismatched project identity", () => {
    expect(() => parseDebugSession(sessionBody, "other")).toThrow();
  });

  it("parses breakpoint text into sorted unique ids", () => {
    expect(parseBreakpointText("review, wait review")).toEqual([
      "review",
      "wait",
    ]);
  });
});

describe("createDebuggerClient", () => {
  it("starts a session, reads status, and posts step_over", async () => {
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      if (path.endsWith("/projects/welcome/debug-sessions") && method === "POST") {
        return jsonResponse(sessionBody, 202);
      }
      if (
        path.endsWith("/projects/welcome/debug-sessions/sess-1") &&
        method === "GET"
      ) {
        return jsonResponse({ session: sessionBody, snapshot: snapshotBody });
      }
      if (
        path.endsWith("/projects/welcome/debug-sessions/sess-1/commands") &&
        method === "POST"
      ) {
        const body = JSON.parse(String(init?.body ?? "{}")) as {
          action: string;
          node_id: string;
        };
        expect(body.action).toBe("step_over");
        expect(body.node_id).toBe("wait");
        return jsonResponse({ command_id: "step-1", revision: 1 });
      }
      throw new Error(`unexpected ${method} ${path}`);
    });
    const client = createDebuggerClient(connection, fetcher);
    const started = await client.startSession("welcome", {
      operation_id: "debug-1",
      source_revision: 1,
      workflow_id: "welcome",
      configuration_revision: "local-v1",
      breakpoints: ["wait"],
      mode: "real",
    });
    expect(started.session_id).toBe("sess-1");
    const status = await client.getStatus("welcome", "sess-1");
    expect(status.snapshot.state).toBe("paused");
    const receipt = await client.sendCommand("welcome", "sess-1", {
      command_id: "step-1",
      node_id: "wait",
      action: "step_over",
      expected_revision: 0,
    });
    expect(receipt).toEqual({ command_id: "step-1", revision: 1 });
    expect(fetcher).toHaveBeenCalled();
    const auth = String(
      (fetcher.mock.calls[0]?.[1]?.headers as Record<string, string>)
        .Authorization,
    );
    expect(auth).toBe("Bearer ui-private-token");
  });

  it("maps forbidden status before parsing", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      jsonResponse({ error: "no" }, 403),
    );
    const client = createDebuggerClient(connection, fetcher);
    await expect(client.listSessions("welcome")).rejects.toBeInstanceOf(
      ProjectApiError,
    );
    await expect(client.listSessions("welcome")).rejects.toMatchObject({
      code: "forbidden",
    });
  });

  it("rejects invalid breakpoint identifiers before network", async () => {
    const fetcher = vi.fn<typeof fetch>();
    const client = createDebuggerClient(connection, fetcher);
    await expect(
      client.startSession("welcome", {
        operation_id: "debug-1",
        source_revision: 1,
        workflow_id: "welcome",
        configuration_revision: "local-v1",
        mode: "real",
        breakpoints: ["bad id"],
      }),
    ).rejects.toBeInstanceOf(ProjectApiError);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
