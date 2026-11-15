import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createPackageClient,
  PackageApiError,
  type MaterializationReceipt,
  type PackageImportMetadata,
  type PackageManifest,
} from "./package-api";
import type { ProjectConnection } from "./project-api";

const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const connection: ProjectConnection = {
  organizationId: "acme",
  environmentId: "uat",
  token: "private.jwt.token",
};

const manifest: PackageManifest = {
  package_schema_version: "0.1",
  organization_id: "acme",
  project_id: "welcome",
  release_id: "rel-1",
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
  release_id: "rel-1",
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
  source_release_id: "rel-1",
  source_revision: 2,
  release_id: "import-aabb",
  revision: 1,
  configuration_revision: "local-v1",
  runtime_version: "proof.v1",
  actor_id: "author",
  created_at: "2026-11-15T00:01:00Z",
  state: "materialized",
};

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("package API critical path", () => {
  it("exports, validates, stages, lists, and materializes with private headers", async () => {
    const zip = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0x00]);
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(
        new Response(zip, {
          status: 200,
          headers: {
            "content-type": "application/zip",
            "X-Package-Digest": digest,
          },
        }),
      )
      .mockResolvedValueOnce(json(manifest))
      .mockResolvedValueOnce(json(staged, 201))
      .mockResolvedValueOnce(
        json({ items: [staged], next_before_revision: null }),
      )
      .mockResolvedValueOnce(json(materialized, 201));

    const client = createPackageClient(connection, fetch);
    const exported = await client.exportPackage("welcome", "rel-1");
    expect(exported.digest).toBe(digest);
    expect(exported.filename).toBe("nexus-flow-project.zip");
    expect(exported.bytes.byteLength).toBe(zip.byteLength);

    expect(await client.validatePackage(zip)).toEqual(manifest);
    expect(await client.stageImport("uat-import-001", zip)).toEqual(staged);
    expect(await client.listImports()).toEqual({
      items: [staged],
      next_before_revision: null,
    });
    expect(
      await client.materialize("uat-import-001", {
        operationId: "uat-materialize-001",
        expectedProjectRevision: 0,
        configurationRevision: "local-v1",
      }),
    ).toEqual(materialized);

    const calls = fetch.mock.calls;
    expect(String(calls[0][0])).toContain(
      "/organizations/acme/environments/uat/projects/welcome/releases/rel-1/package",
    );
    expect(calls[0][1]?.headers).toMatchObject({
      Authorization: "Bearer private.jwt.token",
      Accept: "application/zip",
    });
    expect(calls[1][1]?.method).toBe("POST");
    expect(calls[1][1]?.headers).toMatchObject({
      "Content-Type": "application/zip",
    });
    expect(calls[2][1]?.method).toBe("PUT");
    expect(calls[4][1]?.method).toBe("POST");
    expect(calls[4][1]?.body).toContain('"expected_project_revision":0');
  });

  it("maps forbidden responses without leaking tokens into errors", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response("nope", { status: 403 }),
    );
    const client = createPackageClient(connection, fetch);
    await expect(client.listImports()).rejects.toMatchObject({
      code: "forbidden",
    } satisfies Partial<PackageApiError>);
    expect(String(fetch.mock.calls[0][1]?.headers)).not.toContain("nope");
  });
});
