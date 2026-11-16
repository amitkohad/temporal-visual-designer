import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createDeploymentClient,
  DeploymentApiError,
} from "./deployment-api";
import type { ProjectConnection } from "./project-api";

const connection: ProjectConnection = {
  organizationId: "acme",
  environmentId: "prod",
  token: "private.jwt.token",
};
const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const summary = {
  project_id: "welcome",
  operation_id: "prod-deploy-1",
  release_id: "rel-prod-1",
  revision: 2,
  previous_release_id: "rel-prod-0",
  action: "activate" as const,
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
const attachment = {
  attachment_id: "approval-001",
  display_name: "Approval letter",
  checksum: digest,
  size: 4,
  actor_id: "support",
  created_at: "2026-11-16T00:01:00Z",
};
const response = (value: unknown, statusCode = 200) =>
  new Response(JSON.stringify(value), {
    status: statusCode,
    headers: { "content-type": "application/json" },
  });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("deployment API critical path", () => {
  it("lists history, loads operation detail, uploads and downloads attachments privately", async () => {
    const bytes = new Uint8Array([1, 2, 3, 4]);
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(
        response({
          project_id: "welcome",
          items: [summary],
          next_before_revision: null,
        }),
      )
      .mockResolvedValueOnce(response(summary))
      .mockResolvedValueOnce(response([attachment]))
      .mockResolvedValueOnce(response(attachment, 201))
      .mockResolvedValueOnce(
        new Response(bytes, {
          status: 200,
          headers: { "content-type": "application/octet-stream" },
        }),
      );
    const client = createDeploymentClient(connection, fetch);

    expect(await client.listActivations("welcome", 50)).toEqual({
      project_id: "welcome",
      items: [summary],
      next_before_revision: null,
    });
    expect(
      await client.getActivationOperation("welcome", "prod-deploy-1"),
    ).toEqual(summary);
    expect(
      await client.listAttachments("welcome", "prod-deploy-1"),
    ).toEqual([attachment]);
    expect(
      await client.uploadAttachment(
        "welcome",
        "prod-deploy-1",
        "approval-001",
        bytes,
        "Approval letter",
      ),
    ).toEqual(attachment);
    const downloaded = await client.downloadAttachment(
      "welcome",
      "prod-deploy-1",
      "approval-001",
    );
    expect(downloaded.filename).toBe("deployment-evidence.bin");
    expect(new Uint8Array(downloaded.bytes)).toEqual(bytes);

    const calls = fetch.mock.calls;
    expect(String(calls[0][0])).toContain(
      "/organizations/acme/environments/prod/projects/welcome/activations?limit=50",
    );
    expect(calls[0][1]?.headers).toMatchObject({
      Authorization: "Bearer private.jwt.token",
    });
    expect(String(calls[0][0])).not.toContain("private.jwt.token");
    expect(calls[3][1]?.method).toBe("PUT");
    expect(calls[3][1]?.headers).toMatchObject({
      "Content-Type": "application/octet-stream",
    });
    expect(String(calls[3][0])).toContain("display_name=Approval+letter");
  });

  it("maps forbidden Prod policy failures without leaking response bodies", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(new Response("nope", { status: 403 }));
    const client = createDeploymentClient(connection, fetch);
    await expect(client.listActivations("welcome")).rejects.toMatchObject({
      code: "forbidden",
    } satisfies Partial<DeploymentApiError>);
    expect(String(fetch.mock.calls[0][1]?.headers)).not.toContain("nope");
  });
});
