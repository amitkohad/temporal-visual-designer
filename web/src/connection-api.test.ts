import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createConnectionClient,
  createConnectionWrite,
  mergePickOptions,
  pickOptionsFromFixtures,
  pickOptionsFromRevisions,
  validateHttpsOrigin,
  validateMockFixtureWrite,
} from "./connection-api";
import { ProjectApiError } from "./project-api";

afterEach(() => {
  vi.restoreAllMocks();
});

const connection = {
  organizationId: "local-demo",
  environmentId: "dev",
  token: "test-token",
};

const digest =
  "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("connection helpers", () => {
  it("validates HTTPS DNS origins and mock fixture combinations", () => {
    expect(validateHttpsOrigin("https://api.example.com")).toBeNull();
    expect(validateHttpsOrigin("http://api.example.com")).not.toBeNull();
    expect(validateHttpsOrigin("https://127.0.0.1")).not.toBeNull();
    expect(
      validateMockFixtureWrite({
        method: "GET",
        path: "/ok",
        status_code: 200,
        response_json_body: { ok: true },
      }),
    ).toBeNull();
    expect(
      validateMockFixtureWrite({
        method: "GET",
        path: "/ok",
        status_code: 200,
        json_body: { bad: true },
      }),
    ).not.toBeNull();
  });

  it("builds pick options without leaking private fixture bodies", () => {
    const options = mergePickOptions(
      pickOptionsFromRevisions([
        {
          connection_id: "hr",
          connection_revision: "v1",
          revision: 1,
          connector_type: "http",
          connector_version: "1.0.0",
          definition_digest: digest,
          has_secret_reference: false,
          actor_id: "local",
          created_at: "2026-11-15T00:00:00Z",
        },
      ]),
      pickOptionsFromFixtures([
        {
          connection_id: "hr",
          connection_revision: "v1",
          fixture_digest: digest,
          stored_bytes: 12,
        },
      ]),
    );
    expect(options).toHaveLength(1);
    expect(JSON.stringify(options)).not.toContain("password");
    expect(createConnectionWrite("https://api.example.com", []).base_origin).toBe(
      "https://api.example.com",
    );
  });
});

describe("createConnectionClient", () => {
  it("puts a connection revision and a Dev mock fixture through signed same-origin paths", async () => {
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      const headers = init?.headers as Record<string, string>;
      expect(headers.Authorization).toBe("Bearer test-token");
      expect(path).not.toContain("test-token");
      if (path.endsWith("/connections/hr/revisions/v1") && method === "PUT") {
        const body = JSON.parse(String(init?.body)) as {
          base_origin: string;
        };
        expect(body.base_origin).toBe("https://api.example.com");
        return response(
          {
            scope: {
              organization_id: "local-demo",
              environment_id: "dev",
            },
            connection_id: "hr",
            connection_revision: "v1",
            revision: 1,
            connector_type: "http",
            connector_version: "1.0.0",
            definition_digest: digest,
            has_secret_reference: false,
            actor_id: "local",
            created_at: "2026-11-15T00:00:00Z",
          },
          201,
        );
      }
      if (
        path.endsWith("/connections/hr/revisions/v1/mock-fixture") &&
        method === "PUT"
      ) {
        return response(
          {
            scope: {
              organization_id: "local-demo",
              environment_id: "dev",
            },
            connection_id: "hr",
            connection_revision: "v1",
            fixture_digest: digest,
            stored_bytes: 42,
          },
          201,
        );
      }
      if (path.includes("/mock-fixtures?") && method === "GET") {
        return response({
          items: [
            {
              scope: {
                organization_id: "local-demo",
                environment_id: "dev",
              },
              connection_id: "hr",
              connection_revision: "v1",
              fixture_digest: digest,
              stored_bytes: 42,
            },
          ],
          next_after_connection_id: null,
          next_after_connection_revision: null,
        });
      }
      if (path.endsWith("/connections/hr/revisions?limit=25") && method === "GET") {
        return response({
          items: [
            {
              scope: {
                organization_id: "local-demo",
                environment_id: "dev",
              },
              connection_id: "hr",
              connection_revision: "v1",
              revision: 1,
              connector_type: "http",
              connector_version: "1.0.0",
              definition_digest: digest,
              has_secret_reference: false,
              actor_id: "local",
              created_at: "2026-11-15T00:00:00Z",
            },
          ],
          next_before_revision: null,
        });
      }
      throw new Error(`unexpected ${method} ${path}`);
    });

    const client = createConnectionClient(connection, fetcher);
    const saved = await client.putConnection(
      "hr",
      "v1",
      createConnectionWrite("https://api.example.com", [
        "https://api.example.com",
      ]),
    );
    expect(saved.definition_digest).toBe(digest);
    expect(JSON.stringify(saved)).not.toContain("api.example.com");

    const fixture = await client.putMockFixture("hr", "v1", {
      method: "GET",
      path: "/health",
      status_code: 200,
      response_json_body: { ok: true },
    });
    expect(fixture.fixture_digest).toBe(digest);

    const listed = await client.listMockFixtures();
    expect(listed.items).toHaveLength(1);
    const revisions = await client.listConnectionRevisions("hr");
    expect(revisions.items[0]?.connection_revision).toBe("v1");
  });

  it("maps forbidden administration without echoing provider bodies", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: "secret=leaked" }), {
          status: 403,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = createConnectionClient(connection, fetcher);
    await expect(client.listMockFixtures()).rejects.toMatchObject({
      code: "forbidden",
    } satisfies Partial<ProjectApiError>);
    expect(fetcher.mock.calls[0]?.[0]).toContain("/mock-fixtures?");
  });
});
