import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminCatalog from "../src/AdminCatalog";
import type {
  ActivityImport,
  CatalogClient,
  CatalogEntry,
} from "../src/catalog-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

const connection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "ui-private-token",
};

const manifest = {
  schema_version: "0.1",
  descriptor: {
    descriptor_schema_version: "0.1",
    activity_ref: {
      activity_id: "send-notification",
      version: "1.0.0",
      artifact_digest: digest,
    },
    display_name: "Send notification",
    category: "notifications",
    description: "Send a configured notification",
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    configuration_schema: { type: "object" },
    connection_requirements: [],
    execution_policy: { timeout_seconds: 30, maximum_attempts: 3 },
    runtime_requirements: ["python-3.12"],
    documentation: "docs",
  },
  runtime: {
    language: "python",
    python_version: "3.12",
    entrypoint: "activity:run",
    dependencies: [],
  },
  files: [{ path: "src/activity.py", digest, size: 12 }],
};

const imported: ActivityImport = {
  digest,
  manifest,
  state: "validated",
  author_id: "publisher",
  created_at: "2026-11-15T00:00:00Z",
};

const entry: CatalogEntry = {
  scope: { organization_id: "acme", environment_id: "dev" },
  reference: {
    activity_id: "send-notification",
    version: "1.0.0",
    artifact_digest: digest,
  },
  manifest_json: JSON.stringify(manifest),
  state: "published",
  generation: 1,
  publisher_id: "publisher",
  published_at: "2026-11-15T00:00:00Z",
  updated_by: "publisher",
  updated_at: "2026-11-15T00:00:00Z",
};

function mockClient(overrides: Partial<CatalogClient> = {}): CatalogClient {
  return {
    importBundle: vi.fn().mockResolvedValue(imported),
    readImport: vi.fn().mockResolvedValue(imported),
    publish: vi.fn().mockResolvedValue(entry),
    search: vi.fn().mockResolvedValue([]),
    resolve: vi.fn().mockResolvedValue(entry),
    transition: vi.fn().mockResolvedValue({
      ...entry,
      state: "deprecated",
      generation: 2,
    }),
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AdminCatalog critical path", () => {
  it("imports, publishes, lists, and deprecates through the injected client", async () => {
    const listed: CatalogEntry[] = [];
    const client = mockClient({
      search: vi.fn(async () => [...listed]),
      publish: vi.fn(async () => {
        listed.push(entry);
        return entry;
      }),
      transition: vi.fn(async () => {
        const next = { ...entry, state: "deprecated" as const, generation: 2 };
        listed[0] = next;
        return next;
      }),
    });
    const user = userEvent.setup();
    render(
      <AdminCatalog
        onClose={() => undefined}
        connection={connection}
        client={client}
      />,
    );

    await waitFor(() => expect(client.search).toHaveBeenCalled());
    expect(
      screen.getByRole("heading", { name: "Admin catalog" }),
    ).toBeVisible();

    const file = new File([new Uint8Array([0x50, 0x4b])], "bundle.zip", {
      type: "application/zip",
    });
    await user.upload(screen.getByLabelText("Activity bundle ZIP"), file);
    await waitFor(() => expect(client.importBundle).toHaveBeenCalled());
    expect(
      await screen.findByText(/Validated import Send notification/),
    ).toBeVisible();

    await user.click(
      screen.getByRole("button", { name: "Publish to catalog" }),
    );
    await waitFor(() => expect(client.publish).toHaveBeenCalledWith(digest));
    expect(
      await screen.findByText(/Published Send notification/),
    ).toBeVisible();

    const catalog = screen.getByLabelText("Published catalog");
    const select = within(catalog).getByRole("button", {
      name: /Send notification/i,
    });
    await user.click(select);
    expect(within(catalog).getByText(/Generation 1/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Deprecate" }));
    await waitFor(() =>
      expect(client.transition).toHaveBeenCalledWith(
        entry.reference,
        "deprecated",
        1,
      ),
    );
    expect(
      await screen.findByText(/Deprecated send-notification/),
    ).toBeVisible();
  });

  it("surfaces forbidden publisher policy without mutating the catalog", async () => {
    const { ProjectApiError } = await import("../src/project-api");
    const client = mockClient({
      search: vi.fn().mockRejectedValue(new ProjectApiError("forbidden")),
    });
    render(
      <AdminCatalog
        onClose={() => undefined}
        connection={connection}
        client={client}
      />,
    );
    expect(await screen.findByText(/activity_publisher/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Publish to catalog" }),
    ).not.toBeInTheDocument();
  });
});
