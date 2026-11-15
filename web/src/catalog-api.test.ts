import { afterEach, describe, expect, it, vi } from "vitest";
import {
  activityRefFromImport,
  catalogDisplayLabel,
  createCatalogClient,
  CatalogApiError,
  type ActivityImport,
  type CatalogEntry,
} from "./catalog-api";
import type { ProjectConnection } from "./project-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const connection: ProjectConnection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "private.jwt.token",
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
    documentation: "Uses the notifications connection.",
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

const response = (value: unknown, statusCode = 200) =>
  new Response(JSON.stringify(value), {
    status: statusCode,
    headers: { "content-type": "application/json" },
  });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("catalog API critical path", () => {
  it("imports, publishes, searches, resolves, and transitions with private headers", async () => {
    const zip = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00]);
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response(imported, 201))
      .mockResolvedValueOnce(response(entry, 201))
      .mockResolvedValueOnce(response([entry]))
      .mockResolvedValueOnce(response(entry))
      .mockResolvedValueOnce(
        response({ ...entry, state: "deprecated", generation: 2 }),
      );

    const client = createCatalogClient(connection, fetch);
    expect(await client.importBundle(zip)).toEqual(imported);
    expect(await client.publish(digest)).toEqual(entry);
    expect(await client.search("notify")).toEqual([entry]);
    expect(
      await client.resolve({
        activity_id: "send-notification",
        version: "1.0.0",
        artifact_digest: digest,
      }),
    ).toEqual(entry);
    expect(
      await client.transition(
        {
          activity_id: "send-notification",
          version: "1.0.0",
          artifact_digest: digest,
        },
        "deprecated",
        1,
      ),
    ).toMatchObject({ state: "deprecated", generation: 2 });

    const calls = fetch.mock.calls;
    expect(calls[0][0]).toBe(
      "/api/v1/organizations/acme/environments/dev/activity-imports",
    );
    expect(calls[0][1]?.headers).toMatchObject({
      Authorization: "Bearer private.jwt.token",
      "Content-Type": "application/zip",
      Accept: "application/json",
    });
    expect(calls[0][1]?.credentials).toBe("omit");
    expect(calls[0][1]?.cache).toBe("no-store");
    expect(calls[1][0]).toBe(
      "/api/v1/organizations/acme/environments/dev/activities/publish",
    );
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({
      artifact_digest: digest,
    });
    expect(String(calls[2][0])).toContain("/activities?query=notify");
    expect(calls[3][0]).toBe(
      "/api/v1/organizations/acme/environments/dev/activities/resolve",
    );
    expect(JSON.parse(String(calls[4][1]?.body))).toEqual({
      reference: {
        activity_id: "send-notification",
        version: "1.0.0",
        artifact_digest: digest,
      },
      target: "deprecated",
      expected_generation: 1,
    });
  });

  it("maps authorization failures and rejects unsafe digests", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValue(response({ code: "forbidden" }, 403));
    const client = createCatalogClient(connection, fetch);
    await expect(client.publish(digest)).rejects.toBeInstanceOf(
      CatalogApiError,
    );
    await expect(client.publish(digest)).rejects.toMatchObject({
      code: "forbidden",
    });
    await expect(client.publish("sha256:not-hex")).rejects.toMatchObject({
      code: "invalid_request",
    });
  });

  it("extracts display labels and refs from import/catalog payloads", () => {
    expect(catalogDisplayLabel(imported)).toEqual({
      activityId: "send-notification",
      version: "1.0.0",
      displayName: "Send notification",
      category: "notifications",
    });
    expect(activityRefFromImport(imported)).toEqual({
      activity_id: "send-notification",
      version: "1.0.0",
      artifact_digest: digest,
    });
    expect(catalogDisplayLabel(entry).displayName).toBe("Send notification");
  });
});
