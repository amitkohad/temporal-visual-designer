import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Promote from "../src/Promote";
import type {
  MaterializationReceipt,
  PackageClient,
  PackageImportMetadata,
  PackageManifest,
} from "../src/package-api";
import type { ProjectActivation, ReleaseClient } from "../src/release-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const connection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "ui-private-token",
};

const zipBytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00]).buffer;

const manifest: PackageManifest = {
  package_schema_version: "0.1",
  organization_id: "acme",
  project_id: "welcome",
  release_id: "rel-dev-1",
  source_revision: 2,
  created_at: "2026-11-15T00:00:00Z",
  runtime_version: "proof.v1",
  activity_refs: [],
  content_index: [
    {
      path: "manifest.json",
      size: 12,
      checksum: digest,
      media_type: "application/json",
    },
  ],
  content_digest: digest,
  executable_digest: digest,
  package_digest: digest,
};

const staged: PackageImportMetadata = {
  operation_id: "uat-import-001",
  revision: 1,
  project_id: "welcome",
  release_id: "rel-dev-1",
  package_digest: digest,
  source_revision: 2,
  actor_id: "author",
  created_at: "2026-11-15T00:00:00Z",
  state: "staged",
};

const materialized: MaterializationReceipt = {
  operation_id: "uat-materialize-001",
  import_operation_id: "uat-import-001",
  project_id: "welcome",
  package_digest: digest,
  source_release_id: "rel-dev-1",
  source_revision: 2,
  release_id: "import-dest-1",
  revision: 1,
  configuration_revision: "local-v1",
  runtime_version: "proof.v1",
  actor_id: "author",
  created_at: "2026-11-15T00:01:00Z",
  state: "materialized",
};

const sourceActivation: ProjectActivation = {
  revision: 1,
  release_id: "rel-dev-1",
  project_id: "welcome",
  operation_id: "dev-activate",
  previous_release_id: null,
  runtime_version: "proof.v1",
  configuration_revision: "local-v1",
  checksum: digest,
  actor_id: "author",
  created_at: "2026-11-15T00:00:00Z",
};

const targetActivation: ProjectActivation = {
  revision: 1,
  release_id: "import-dest-1",
  project_id: "welcome",
  operation_id: "uat-activate-001",
  previous_release_id: null,
  runtime_version: "proof.v1",
  configuration_revision: "local-v1",
  checksum: digest,
  actor_id: "author",
  created_at: "2026-11-15T00:02:00Z",
};

function mockPackageClient(
  overrides: Partial<PackageClient> = {},
): PackageClient {
  return {
    exportPackage: vi.fn().mockResolvedValue({
      bytes: zipBytes,
      digest,
      filename: "nexus-flow-project.zip",
    }),
    validatePackage: vi.fn().mockResolvedValue(manifest),
    stageImport: vi.fn().mockResolvedValue(staged),
    listImports: vi.fn().mockResolvedValue({
      items: [],
      next_before_revision: null,
    }),
    getImport: vi.fn().mockResolvedValue(staged),
    getImportManifest: vi.fn().mockResolvedValue(manifest),
    materialize: vi.fn().mockResolvedValue(materialized),
    getMaterialization: vi.fn().mockResolvedValue(materialized),
    ...overrides,
  };
}

function mockReleaseClient(
  overrides: Partial<ReleaseClient> = {},
): ReleaseClient {
  return {
    publish: vi.fn(),
    getActivation: vi.fn().mockResolvedValue({ revision: 0, release_id: null }),
    activate: vi.fn().mockResolvedValue(targetActivation),
    start: vi.fn(),
    getExecution: vi.fn(),
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Promote critical path", () => {
  it("exports, stages, materializes, and activates through injected clients", async () => {
    const listed: PackageImportMetadata[] = [];
    const source = mockPackageClient();
    const target = mockPackageClient({
      listImports: vi.fn(async () => ({
        items: [...listed],
        next_before_revision: null,
      })),
      stageImport: vi.fn(async (operationId: string) => {
        const next = { ...staged, operation_id: operationId };
        listed.splice(0, listed.length, next);
        return next;
      }),
      materialize: vi.fn(async () => materialized),
    });
    const sourceRelease = mockReleaseClient({
      getActivation: vi.fn().mockResolvedValue(sourceActivation),
    });
    const release = mockReleaseClient({
      getActivation: vi.fn().mockResolvedValue({ revision: 0, release_id: null }),
      activate: vi.fn().mockResolvedValue(targetActivation),
    });
    const user = userEvent.setup();
    render(
      <Promote
        onClose={() => undefined}
        connection={connection}
        sourceClient={source}
        targetClient={target}
        sourceReleaseClient={sourceRelease}
        releaseClient={release}
      />,
    );

    await waitFor(() => expect(target.listImports).toHaveBeenCalled());
    expect(screen.getByRole("heading", { name: "Promote" })).toBeVisible();
    expect(
      screen.getByText(/Staging does not activate/i),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Use active release" }),
    );
    await waitFor(() =>
      expect(sourceRelease.getActivation).toHaveBeenCalledWith("welcome"),
    );
    expect(screen.getByLabelText("Release ID")).toHaveValue("rel-dev-1");

    await user.click(screen.getByRole("button", { name: "Export package" }));
    await waitFor(() =>
      expect(source.exportPackage).toHaveBeenCalledWith("welcome", "rel-dev-1"),
    );
    expect(await screen.findByText(/Exported nexus-flow-project.zip/)).toBeVisible();

    const importOp = screen.getByLabelText("Import operation ID");
    await user.clear(importOp);
    await user.type(importOp, "uat-import-001");

    await user.click(screen.getByRole("button", { name: "Validate package" }));
    await waitFor(() => expect(target.validatePackage).toHaveBeenCalled());
    expect(
      await screen.findByText(/Validated welcome release rel-dev-1/),
    ).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Stage import" }));
    await waitFor(() =>
      expect(target.stageImport).toHaveBeenCalledWith(
        "uat-import-001",
        zipBytes,
      ),
    );
    expect(await screen.findByText(/Staged import uat-import-001/)).toBeVisible();

    const matPanel = screen.getByLabelText("Materialize destination release");
    const matOp = within(matPanel).getByLabelText(
      "Materialization operation ID",
    );
    await user.clear(matOp);
    await user.type(matOp, "uat-materialize-001");
    await user.click(
      within(matPanel).getByRole("button", { name: "Materialize release" }),
    );
    await waitFor(() =>
      expect(target.materialize).toHaveBeenCalledWith("uat-import-001", {
        operationId: "uat-materialize-001",
        expectedProjectRevision: 0,
        configurationRevision: "local-v1",
      }),
    );
    expect(
      await screen.findByText(/Materialized destination release import-dest-1/),
    ).toBeVisible();

    const actPanel = screen.getByLabelText("Activate in target");
    const actOp = within(actPanel).getByLabelText("Activation operation ID");
    await user.clear(actOp);
    await user.type(actOp, "uat-activate-001");
    await user.click(
      within(actPanel).getByRole("button", { name: "Activate release" }),
    );
    await waitFor(() =>
      expect(release.activate).toHaveBeenCalledWith("welcome", {
        releaseId: "import-dest-1",
        configurationRevision: "local-v1",
        operationId: "uat-activate-001",
        expectedRevision: 0,
        runtimeVersion: "proof.v1",
        action: "activate",
      }),
    );
    expect(
      await screen.findByText(/Activated import-dest-1 in uat/),
    ).toBeVisible();
  });

  it("surfaces missing UAT grant without claiming Entra multi-tenant readiness", async () => {
    const { ProjectApiError } = await import("../src/project-api");
    const target = mockPackageClient({
      listImports: vi
        .fn()
        .mockRejectedValue(new ProjectApiError("forbidden")),
    });
    render(
      <Promote
        onClose={() => undefined}
        connection={connection}
        sourceClient={mockPackageClient()}
        targetClient={target}
        sourceReleaseClient={mockReleaseClient()}
        releaseClient={mockReleaseClient()}
      />,
    );
    expect(
      await screen.findByText(/business_author on UAT/i),
    ).toBeVisible();
    expect(screen.getByText(/not multi-tenant Entra/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Stage import" }),
    ).toBeInTheDocument();
  });
});
