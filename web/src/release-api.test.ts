import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createReleaseClient,
  newIdempotencyKey,
  ReleaseApiError,
} from "./release-api";
import type { ProjectConnection } from "./project-api";

const connection: ProjectConnection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "private.jwt.token",
};
const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const release = {
  project_id: "hr",
  release_id: "rel-1",
  source_revision: 2,
  checksum: digest,
};
const activation = {
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
};
const accepted = {
  execution_id: "run-1",
  release_id: "rel-1",
  configuration_revision: "local-v1",
  state: "accepted",
};
const status = {
  release_id: "rel-1",
  definition_digest: digest,
  runtime_version: "proof.v1",
  node_id: "review",
  state: "completed",
  revision: 3,
  completed_nodes: ["start", "review"],
};
const response = (value: unknown, statusCode = 200) =>
  new Response(JSON.stringify(value), {
    status: statusCode,
    headers: { "content-type": "application/json" },
  });
const fetcher = (value: unknown, statusCode = 200) =>
  vi.fn<typeof globalThis.fetch>().mockResolvedValue(response(value, statusCode));

afterEach(() => vi.useRealTimers());

describe("release API critical path", () => {
  it("publishes, activates, starts, and reads status with private request policy", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockResolvedValueOnce(response(release, 201))
      .mockResolvedValueOnce(response({ revision: 0, release_id: null }))
      .mockResolvedValueOnce(response(activation))
      .mockResolvedValueOnce(response(accepted, 202))
      .mockResolvedValueOnce(response(status));
    const client = createReleaseClient(connection, fetch);

    expect(
      await client.publish("hr", 2, "pub-key-1"),
    ).toEqual(release);
    expect(await client.getActivation("hr")).toEqual({
      revision: 0,
      release_id: null,
    });
    expect(
      await client.activate("hr", {
        releaseId: "rel-1",
        configurationRevision: "local-v1",
        operationId: "act-1",
        expectedRevision: 0,
      }),
    ).toEqual(activation);
    expect(
      await client.start("hr", {
        workflowId: "onboarding",
        idempotencyKey: "run-key-1",
      }),
    ).toEqual(accepted);
    expect(await client.getExecution("hr", "run-1")).toEqual(status);

    const calls = fetch.mock.calls;
    expect(calls[0][0]).toBe(
      "/api/v1/organizations/acme/environments/dev/projects/hr/releases",
    );
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({
      source_revision: 2,
      idempotency_key: "pub-key-1",
    });
    expect(calls[0][1]).toMatchObject({
      method: "POST",
      headers: { Authorization: "Bearer private.jwt.token" },
      credentials: "omit",
      cache: "no-store",
      redirect: "error",
    });
    expect(JSON.parse(String(calls[2][1]?.body))).toEqual({
      project_id: "hr",
      release_id: "rel-1",
      configuration_revision: "local-v1",
      operation_id: "act-1",
      expected_revision: 0,
      runtime_version: "proof.v1",
      action: "activate",
    });
    expect(JSON.parse(String(calls[3][1]?.body))).toEqual({
      workflow_id: "onboarding",
      idempotency_key: "run-key-1",
      input: {},
      source: "manual",
    });
    expect(calls[4][0]).toMatch(/\/executions\/run-1$/);
    expect(JSON.stringify(calls.map(([url]) => url))).not.toContain("private");
  });

  it("reuses caller idempotency keys and maps HTTP failures privately", async () => {
    const fetch = fetcher({ code: "conflict" }, 409);
    await expect(
      createReleaseClient(connection, fetch).publish("hr", 1, "same-key"),
    ).rejects.toMatchObject({ code: "conflict" });
    expect(JSON.parse(String(fetch.mock.calls[0][1]?.body)).idempotency_key).toBe(
      "same-key",
    );
    await expect(
      createReleaseClient(connection, fetcher({}, 401)).start("hr", {
        workflowId: "onboarding",
        idempotencyKey: "k",
      }),
    ).rejects.toBeInstanceOf(ReleaseApiError);
  });

  it("rejects invalid ids without issuing requests", async () => {
    const fetch = fetcher(release, 201);
    await expect(
      createReleaseClient(connection, fetch).publish("../hr", 1, "k"),
    ).rejects.toMatchObject({ code: "invalid_request" });
    expect(fetch).not.toHaveBeenCalled();
    expect(newIdempotencyKey("pub")).toMatch(/^pub-/);
  });
});
