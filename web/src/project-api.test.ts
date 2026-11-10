import { afterEach, describe, expect, it, vi } from "vitest";
import { createProjectClient, ProjectApiError } from "./project-api";
import type { JsonObject, ProjectConnection } from "./project-api";

const connection: ProjectConnection = {
  organizationId: "acme",
  environmentId: "dev",
  token: "private.jwt.token",
};
const content: JsonObject = {
  schema_version: "0.1",
  workflows: [],
  forms: [],
  layout: { keep: "opaque" },
};
const result = (revision = 1, value = content) => ({
  project_id: "hr",
  revision,
  parent_revision: revision === 1 ? null : revision - 1,
  summary: "created",
  author_id: "alice",
  created_at: "2026-11-10T09:00:00+00:00",
  content: value,
});
const response = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
const fetcher = (value: unknown) =>
  vi.fn<typeof globalThis.fetch>().mockResolvedValue(response(value));
afterEach(() => vi.useRealTimers());

describe("private same-origin project API", () => {
  it("snapshots connection scope/token and uses fixed private request policy", async () => {
    const original = { ...connection };
    const fetch = fetcher(result());
    const client = createProjectClient(original, fetch);
    original.token = "changed";
    original.organizationId = "changed";
    const actual = await client.readProject("hr");
    expect(actual).toEqual(result());
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(
      "/api/v1/organizations/acme/environments/dev/projects/hr",
    );
    expect(fetch.mock.calls[0][1]).toMatchObject({
      method: "GET",
      headers: { Authorization: "Bearer private.jwt.token" },
      redirect: "error",
      cache: "no-store",
      credentials: "omit",
      referrerPolicy: "no-referrer",
    });
    expect(JSON.stringify(fetch.mock.calls[0][0])).not.toContain("private");
    actual.content.layout = { changed: true };
    expect(content.layout).toEqual({ keep: "opaque" });
  });
  it("validates exact scoped project list and returns detached metadata only", async () => {
    const fetch = fetcher([
      {
        scope: { organization_id: "acme", environment_id: "dev" },
        project_id: "hr",
        name: "HR",
        head_revision: 1,
        unknown: "private",
      },
    ]);
    expect(await createProjectClient(connection, fetch).listProjects()).toEqual(
      [{ project_id: "hr", name: "HR", head_revision: 1 }],
    );
    expect(fetch.mock.calls[0][0]).toMatch(/projects\?limit=100&offset=0$/);
  });
  it.each([
    [
      {
        scope: { organization_id: "other", environment_id: "dev" },
        project_id: "hr",
        name: "HR",
        head_revision: 1,
      },
    ],
    [
      {
        scope: { organization_id: "acme", environment_id: "prod" },
        project_id: "hr",
        name: "HR",
        head_revision: 1,
      },
    ],
    [{ project_id: "hr", name: "HR", head_revision: 1 }],
    [
      {
        scope: { organization_id: "acme", environment_id: "dev" },
        project_id: "../hr",
        name: "HR",
        head_revision: 1,
      },
    ],
    [
      {
        scope: { organization_id: "acme", environment_id: "dev" },
        project_id: "hr",
        name: "HR",
        head_revision: Number.MAX_SAFE_INTEGER + 1,
      },
    ],
    { items: [] },
  ])("rejects malformed or foreign listing %#", async (value) => {
    await expect(
      createProjectClient(connection, fetcher(value)).listProjects(),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
  it.each([
    { organizationId: "../other" },
    { organizationId: ".." },
    { environmentId: "." },
    { environmentId: "dev?scope=prod" },
    { token: "private\r\nheader" },
    { token: "" },
    { token: "private token" },
    { token: "a".repeat(16385) },
  ])("rejects malformed connection %# without requests", (changes) => {
    const fetch = fetcher(result());
    expect(() =>
      createProjectClient({ ...connection, ...changes }, fetch),
    ).toThrow(ProjectApiError);
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    ["", 0],
    [".", 1],
    ["..", 1],
    ["../hr", 1],
    ["hr", 0],
    ["hr", -1],
    ["hr", 1.1],
    ["hr", Number.MAX_SAFE_INTEGER],
  ])("rejects invalid save id/revision %#", async (id, revision) => {
    const fetch = fetcher(result());
    await expect(
      createProjectClient(connection, fetch).saveProject(
        String(id),
        Number(revision),
        content,
        "layout",
      ),
    ).rejects.toMatchObject({ code: "invalid_request" });
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([401, 403, 404, 409, 422, 500, 503])(
    "returns closed private status %i",
    async (status) => {
      const fetch = vi
        .fn<typeof globalThis.fetch>()
        .mockResolvedValue(response({ message: connection.token }, status));
      const failure = await createProjectClient(connection, fetch)
        .readProject("hr")
        .catch((error) => error);
      expect(failure).toBeInstanceOf(ProjectApiError);
      expect(String(failure)).toBe("ProjectApiError: Project request failed");
      expect(failure.message).not.toContain("private");
      expect(failure.code).toBe(
        (
          {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "invalid_request",
          } as Record<number, string>
        )[status] ?? "unavailable",
      );
      expect(fetch).toHaveBeenCalledOnce();
    },
  );
  it.each([
    { ...result(), project_id: "foreign" },
    { ...result(), revision: 0 },
    { ...result(), parent_revision: 1 },
    { ...result(), created_at: "2026-02-30T09:00:00Z" },
    { ...result(), created_at: "2026-11-10T09:00:00+01:00" },
    { ...result(), content: { schema_version: "0.2", workflows: [] } },
    { ...result(), content: { ...content, value: 9007199254740992 } },
  ])("rejects malformed and non-lossless read snapshots %#", async (value) => {
    await expect(
      createProjectClient(connection, fetcher(value)).readProject("hr"),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("preserves opaque JSON including prototype-named own keys in a save", async () => {
    const original = JSON.parse(
      '{"schema_version":"0.1","workflows":[],"layout":{"__proto__":{"value":"private"},"constructor":{"keep":true}}}',
    ) as JsonObject;
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementation(async (_path, options) => {
        const body = JSON.parse(String(options?.body));
        expect(body).toEqual({
          expected_revision: 1,
          summary: "layout",
          content: original,
        });
        expect(body.content.layout.__proto__).toEqual({ value: "private" });
        return response(result(2, body.content));
      });
    const saved = await createProjectClient(connection, fetch).saveProject(
      "hr",
      1,
      original,
      "layout",
    );
    expect(saved.content).toEqual(original);
    expect(Object.prototype).not.toHaveProperty("value");
  });
  it("captures save payload before awaiting transport and accepts reordered acknowledgement objects", async () => {
    const original = structuredClone(content);
    let release: ((value: Response) => void) | undefined;
    const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const waiting = createProjectClient(connection, fetch).saveProject(
      "hr",
      1,
      original,
      "layout",
    );
    original.layout = { mutated: true };
    release!(
      response({
        ...result(2),
        content: {
          layout: { keep: "opaque" },
          forms: [],
          workflows: [],
          schema_version: "0.1",
        },
      }),
    );
    expect((await waiting).content).toEqual(content);
  });
  it.each([
    result(1),
    result(3),
    { ...result(2), project_id: "foreign" },
    result(2, { ...content, layout: { changed: true } }),
  ])("rejects mismatched save acknowledgement %#", async (value) => {
    await expect(
      createProjectClient(connection, fetcher(value)).saveProject(
        "hr",
        1,
        content,
        "layout",
      ),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("bounds response wire bytes even without content-length", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
      new Response("x".repeat(6 * 1024 * 1024 + 1), {
        headers: { "content-type": "application/json" },
      }),
    );
    await expect(
      createProjectClient(connection, fetch).readProject("hr"),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("bounds request bytes without entering transport", async () => {
    const fetch = fetcher(result());
    await expect(
      createProjectClient(connection, fetch).saveProject(
        "hr",
        1,
        { ...content, data: "x".repeat(6 * 1024 * 1024) },
        "layout",
      ),
    ).rejects.toMatchObject({ code: "invalid_request" });
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    new Response("private invalid JSON", {
      headers: { "content-type": "application/json" },
    }),
    new Response("{}", { headers: { "content-type": "text/html" } }),
    new Response("{}", {
      headers: {
        "content-type": "application/json",
        "content-length": "99999999",
      },
    }),
  ])("rejects invalid response framing %#", async (value) => {
    await expect(
      createProjectClient(
        connection,
        vi.fn<typeof globalThis.fetch>().mockResolvedValue(value),
      ).readProject("hr"),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("times out stalled ports and aborts without retry or releasing a late response", async () => {
    vi.useFakeTimers();
    let release: ((value: Response) => void) | undefined;
    const fetch = vi.fn<typeof globalThis.fetch>().mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const observed = createProjectClient(connection, fetch)
      .readProject("hr")
      .catch((error) => error);
    await vi.advanceTimersByTimeAsync(10000);
    expect(await observed).toMatchObject({ code: "unavailable" });
    expect(fetch.mock.calls[0][1]?.signal?.aborted).toBe(true);
    release!(response(result()));
    expect(fetch).toHaveBeenCalledOnce();
  });
  it("does not leak network diagnostics", async () => {
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockRejectedValue(new Error(connection.token));
    await expect(
      createProjectClient(connection, fetch).readProject("hr"),
    ).rejects.toMatchObject({
      code: "unavailable",
      message: "Project request failed",
    });
  });
  it("cancels a stalled response body and releases its reader when the deadline expires", async () => {
    vi.useFakeTimers();
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ cancel });
    const wire = new Response(stream, {
      headers: { "content-type": "application/json" },
    });
    const mockFetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(wire);
    const observed = createProjectClient(connection, mockFetch)
      .readProject("hr")
      .catch((error) => error);
    await vi.advanceTimersByTimeAsync(0);
    expect(stream.locked).toBe(true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(await observed).toMatchObject({ code: "unavailable" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(stream.locked).toBe(false);
    expect(mockFetch).toHaveBeenCalledOnce();
  });
  it("cancels unsupported bodies before taking a reader", async () => {
    const cancel = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ cancel });
    const wire = new Response(stream, {
      headers: { "content-type": "text/html" },
    });
    await expect(
      createProjectClient(
        connection,
        vi.fn<typeof globalThis.fetch>().mockResolvedValue(wire),
      ).readProject("hr"),
    ).rejects.toMatchObject({ code: "invalid_response" });
    expect(cancel).toHaveBeenCalledOnce();
    expect(stream.locked).toBe(false);
  });
  it.each(["1.0", "-0", "-0.0", "1e0", "1e-07", "9007199254740993"])(
    "rejects wire numeric type/token drift %s",
    async (number) => {
      const text = JSON.stringify(result()).replace(
        '"keep":"opaque"',
        '"keep":"opaque","numeric":' + number,
      );
      const fetch = vi.fn<typeof globalThis.fetch>().mockResolvedValue(
        new Response(text, {
          headers: { "content-type": "application/json" },
        }),
      );
      await expect(
        createProjectClient(connection, fetch).readProject("hr"),
      ).rejects.toMatchObject({ code: "invalid_response" });
    },
  );
  it("ignores number-like text and escaped quotes inside business strings", async () => {
    const value = {
      ...content,
      data: '1.0 \\"-0" 9007199254740993 1e-07',
      numeric: 1.5,
    };
    expect(
      (
        await createProjectClient(
          connection,
          fetcher(result(1, value)),
        ).readProject("hr")
      ).content,
    ).toEqual(value);
  });
});
