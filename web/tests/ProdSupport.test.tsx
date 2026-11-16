import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ProdSupport from "../src/ProdSupport";
import type {
  ActivationSummary,
  AttachmentMetadata,
  DeploymentClient,
} from "../src/deployment-api";
import type { ProjectActivation, ReleaseClient } from "../src/release-api";
import { ProjectApiError } from "../src/project-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const connection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "ui-private-token",
};

const summary: ActivationSummary = {
  project_id: "welcome",
  operation_id: "prod-deploy-1",
  release_id: "rel-prod-1",
  revision: 1,
  previous_release_id: null,
  action: "activate",
  runtime_version: "proof.v1",
  configuration_revision: "local-v1",
  checksum: digest,
  actor_id: "support",
  created_at: "2026-11-16T00:00:00Z",
  evidence: {
    change_request_number: "CHG-1042",
    notes: "Offline approval recorded",
  },
};

const activation: ProjectActivation = {
  revision: 1,
  release_id: "rel-prod-1",
  project_id: "welcome",
  operation_id: "prod-deploy-1",
  previous_release_id: null,
  runtime_version: "proof.v1",
  configuration_revision: "local-v1",
  checksum: digest,
  actor_id: "support",
  created_at: "2026-11-16T00:00:00Z",
  evidence: summary.evidence,
};

const attachment: AttachmentMetadata = {
  attachment_id: "approval-001",
  display_name: "Approval letter",
  checksum: digest,
  size: 4,
  actor_id: "support",
  created_at: "2026-11-16T00:01:00Z",
};

function mockDeploymentClient(
  overrides: Partial<DeploymentClient> = {},
): DeploymentClient {
  return {
    listActivations: vi.fn().mockResolvedValue({
      project_id: "welcome",
      items: [summary],
      next_before_revision: null,
    }),
    getActivationOperation: vi.fn().mockResolvedValue(summary),
    listAttachments: vi.fn().mockResolvedValue([]),
    uploadAttachment: vi.fn().mockResolvedValue(attachment),
    downloadAttachment: vi.fn().mockResolvedValue({
      bytes: new Uint8Array([1, 2, 3, 4]).buffer,
      filename: "deployment-evidence.bin",
      contentType: "application/octet-stream",
    }),
    ...overrides,
  };
}

function mockReleaseClient(
  overrides: Partial<ReleaseClient> = {},
): ReleaseClient {
  return {
    publish: vi.fn(),
    getActivation: vi.fn().mockResolvedValue(activation),
    activate: vi.fn().mockResolvedValue({
      ...activation,
      revision: 2,
      operation_id: "prod-activate-new",
      evidence: {
        change_request_number: "CHG-2000",
        notes: "Prod cutover",
      },
    }),
    start: vi.fn(),
    getExecution: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Prod support critical path", () => {
  it("activates with evidence, lists history, and uploads an attachment", async () => {
    const listed: ActivationSummary[] = [summary];
    const files: AttachmentMetadata[] = [];
    const deployment = mockDeploymentClient({
      listActivations: vi.fn(async () => ({
        project_id: "welcome",
        items: [...listed],
        next_before_revision: null,
      })),
      getActivationOperation: vi.fn(async (_project, operationId) => {
        const found = listed.find((item) => item.operation_id === operationId);
        if (!found) throw new ProjectApiError("not_found");
        return found;
      }),
      listAttachments: vi.fn(async () => [...files]),
      uploadAttachment: vi.fn(async (_p, _o, attachmentId) => {
        const next = { ...attachment, attachment_id: attachmentId };
        files.splice(0, files.length, next);
        return next;
      }),
    });
    const release = mockReleaseClient({
      activate: vi.fn(async (_project, params) => {
        const created: ActivationSummary = {
          ...summary,
          release_id: params.releaseId,
          operation_id: params.operationId,
          revision: 2,
          evidence: params.evidence ?? undefined,
        };
        listed.unshift(created);
        return {
          ...activation,
          release_id: params.releaseId,
          revision: 2,
          operation_id: params.operationId,
          evidence: params.evidence ?? undefined,
        };
      }),
    });
    const user = userEvent.setup();
    render(
      <ProdSupport
        onClose={() => undefined}
        connection={connection}
        deploymentClient={deployment}
        releaseClient={release}
      />,
    );

    await waitFor(() =>
      expect(deployment.listActivations).toHaveBeenCalledWith("welcome", 50),
    );
    expect(screen.getByRole("heading", { name: "Prod support" })).toBeVisible();
    expect(
      screen.getByText(/production_support grant/i),
    ).toBeVisible();
    expect(
      screen.getByRole("button", { name: /prod-deploy-1/i }),
    ).toBeVisible();

    const actPanel = screen.getByLabelText("Activate with optional evidence");
    const releaseField = within(actPanel).getByLabelText("Release ID");
    await user.clear(releaseField);
    await user.type(releaseField, "rel-prod-2");
    const opField = within(actPanel).getByLabelText("Activation operation ID");
    await user.clear(opField);
    await user.type(opField, "prod-activate-new");
    await user.type(
      within(actPanel).getByLabelText("Change request (optional)"),
      "CHG-2000",
    );
    await user.type(
      within(actPanel).getByLabelText("Notes (optional)"),
      "Prod cutover",
    );
    await user.click(
      within(actPanel).getByRole("button", { name: "Activate release" }),
    );

    await waitFor(() =>
      expect(release.activate).toHaveBeenCalledWith("welcome", {
        releaseId: "rel-prod-2",
        configurationRevision: "local-v1",
        operationId: "prod-activate-new",
        expectedRevision: 1,
        runtimeVersion: "proof.v1",
        action: "activate",
        evidence: {
          change_request_number: "CHG-2000",
          notes: "Prod cutover",
        },
      }),
    );
    expect(
      await screen.findByText(/Activated rel-prod-2 in prod/i),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: /prod-activate-new/i }),
    );
    await waitFor(() =>
      expect(deployment.getActivationOperation).toHaveBeenCalledWith(
        "welcome",
        "prod-activate-new",
      ),
    );

    const file = new File([new Uint8Array([1, 2, 3, 4])], "approval.bin", {
      type: "application/octet-stream",
    });
    const attPanel = screen.getByLabelText("Evidence attachments");
    const attId = within(attPanel).getByLabelText("Attachment ID");
    await user.clear(attId);
    await user.type(attId, "approval-001");
    const input = attPanel.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await user.upload(input, file);

    await waitFor(() =>
      expect(deployment.uploadAttachment).toHaveBeenCalledWith(
        "welcome",
        "prod-activate-new",
        "approval-001",
        expect.any(File),
        "Approval evidence",
      ),
    );
    expect(await screen.findByText(/Uploaded approval-001/)).toBeVisible();
  });

  it("surfaces missing Prod production_support grant clearly", async () => {
    const deployment = mockDeploymentClient({
      listActivations: vi
        .fn()
        .mockRejectedValue(new ProjectApiError("forbidden")),
    });
    render(
      <ProdSupport
        onClose={() => undefined}
        connection={connection}
        deploymentClient={deployment}
        releaseClient={mockReleaseClient()}
      />,
    );
    expect(
      await screen.findByText(/production_support on Prod/i),
    ).toBeVisible();
    expect(screen.getByText(/not multi-tenant Entra/i)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Activate release" }),
    ).toBeInTheDocument();
  });
});
