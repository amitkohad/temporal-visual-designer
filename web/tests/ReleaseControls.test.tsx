import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ReleaseControls from "../src/ReleaseControls";
import type { ReleaseClient } from "../src/release-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const connection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "ui-private-token",
};

function mockClient(overrides: Partial<ReleaseClient> = {}): ReleaseClient {
  return {
    publish: vi.fn().mockResolvedValue({
      project_id: "hr",
      release_id: "rel-1",
      source_revision: 3,
      checksum: digest,
    }),
    getActivation: vi.fn().mockResolvedValue({ revision: 0, release_id: null }),
    activate: vi.fn().mockResolvedValue({
      revision: 1,
      release_id: "rel-1",
      project_id: "hr",
      operation_id: "act-1",
      previous_release_id: null,
      runtime_version: "proof.v1",
      configuration_revision: "local-v1",
      checksum: digest,
      actor_id: "alice",
      created_at: "2026-11-14T10:00:00+00:00",
    }),
    start: vi.fn().mockResolvedValue({
      execution_id: "run-42",
      release_id: "rel-1",
      configuration_revision: "local-v1",
      state: "accepted",
    }),
    getExecution: vi.fn().mockResolvedValue({
      release_id: "rel-1",
      definition_digest: digest,
      runtime_version: "proof.v1",
      node_id: "review",
      state: "waiting_approval",
      revision: 1,
      completed_nodes: ["start"],
    }),
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReleaseControls critical path", () => {
  it("blocks publish when validationOk is false", async () => {
    const client = mockClient();
    render(
      <ReleaseControls
        connection={connection}
        projectId="hr"
        headRevision={3}
        workflowId="onboarding"
        validationOk={false}
        client={client}
      />,
    );
    expect(
      screen.getByText(/Publish is blocked until Designer validation passes/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Publish release" }),
    ).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Publish release" }),
    );
    expect(client.publish).not.toHaveBeenCalled();
  });

  it("publishes, activates, starts, polls status, and offers task deep-link", async () => {
    const client = mockClient();
    const onActivated = vi.fn();
    const onStarted = vi.fn();
    const onOpenTasks = vi.fn();
    const user = userEvent.setup();

    render(
      <ReleaseControls
        connection={connection}
        projectId="hr"
        headRevision={3}
        workflowId="onboarding"
        validationOk
        client={client}
        pollIntervalMs={20}
        onActivated={onActivated}
        onStarted={onStarted}
        onOpenTasks={onOpenTasks}
      />,
    );

    await waitFor(() => expect(client.getActivation).toHaveBeenCalledWith("hr"));

    await user.click(screen.getByRole("button", { name: "Publish release" }));
    await waitFor(() =>
      expect(client.publish).toHaveBeenCalledWith(
        "hr",
        3,
        expect.stringMatching(/^pub-/),
      ),
    );
    expect(await screen.findByText("rel-1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Activate" }));
    await waitFor(() =>
      expect(client.activate).toHaveBeenCalledWith("hr", {
        releaseId: "rel-1",
        configurationRevision: "local-v1",
        operationId: expect.stringMatching(/^act-/),
        expectedRevision: 0,
      }),
    );
    expect(onActivated).toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Start execution" }));
    await waitFor(() =>
      expect(client.start).toHaveBeenCalledWith("hr", {
        workflowId: "onboarding",
        idempotencyKey: expect.stringMatching(/^run-/),
        input: {},
        source: "manual",
      }),
    );
    expect(onStarted).toHaveBeenCalledWith("run-42");
    await waitFor(() =>
      expect(client.getExecution).toHaveBeenCalledWith("hr", "run-42"),
    );
    expect(
      await screen.findByText(/waiting_approval · node review/),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Open tasks for this run" }),
    );
    expect(onOpenTasks).toHaveBeenCalledWith("run-42");
  });
});
