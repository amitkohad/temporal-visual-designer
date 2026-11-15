import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createConnectionClient,
  createConnectionWrite,
  createEmailConnectionWrite,
  mergePickOptions,
  pickOptionsFromEmailRevisions,
  pickOptionsFromFixtures,
  pickOptionsFromRevisions,
  validateEmailConnectionWrite,
  validateHttpsOrigin,
  validateMockFixtureWrite,
  validateRecipientDomain,
  validateSenderEmail,
  validateSmtpHost,
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

  it("validates email SMTP host, sender, and recipient domain shapes", () => {
    expect(validateSmtpHost("MAIL.Example.Test")).toBeNull();
    expect(validateSmtpHost("127.0.0.1")).not.toBeNull();
    expect(validateSmtpHost("mail.example.com:465")).not.toBeNull();
    expect(validateSenderEmail("Onboard@EXAMPLE.TEST")).toBeNull();
    expect(validateSenderEmail("not-an-email")).not.toBeNull();
    expect(validateRecipientDomain("partner.test")).toBeNull();
    expect(validateRecipientDomain("*.example.test")).not.toBeNull();
    const write = createEmailConnectionWrite(
      "SMTP.Private.example.test",
      "Onboard@EXAMPLE.TEST",
      ["other.test", "EXAMPLE.TEST", "other.test"],
      { port: 465, secretReference: "opaque-private-reference" },
    );
    expect(write).toMatchObject({
      connector_type: "email",
      transport: "smtp_tls",
      smtp_host: "smtp.private.example.test",
      sender: "Onboard@example.test",
      allowed_recipient_domains: ["example.test", "other.test"],
      secret_reference: "opaque-private-reference",
    });
    expect(validateEmailConnectionWrite(write)).toBeNull();
    expect(
      validateEmailConnectionWrite({
        ...write,
        allowed_recipient_domains: [],
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
      pickOptionsFromEmailRevisions([
        {
          connection_id: "hr-mail",
          connection_revision: "v1",
          revision: 1,
          connector_type: "email",
          connector_version: "1.0.0",
          transport: "smtp_tls",
          definition_digest: digest,
          has_secret_reference: true,
          actor_id: "local",
          created_at: "2026-11-15T00:00:00Z",
        },
      ]),
    );
    expect(options).toHaveLength(2);
    expect(JSON.stringify(options)).not.toContain("password");
    expect(JSON.stringify(options)).not.toContain("smtp.private");
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
      if (
        path.endsWith("/email-connections/hr-mail/revisions/v1") &&
        method === "PUT"
      ) {
        const body = JSON.parse(String(init?.body)) as {
          smtp_host: string;
          sender: string;
          transport: string;
        };
        expect(body.transport).toBe("smtp_tls");
        expect(body.smtp_host).toBe("smtp.private.example.test");
        expect(body.sender).toBe("Onboard@example.test");
        return response(
          {
            scope: {
              organization_id: "local-demo",
              environment_id: "dev",
            },
            connection_id: "hr-mail",
            connection_revision: "v1",
            revision: 1,
            connector_type: "email",
            connector_version: "1.0.0",
            transport: "smtp_tls",
            definition_digest: digest,
            has_secret_reference: true,
            actor_id: "local",
            created_at: "2026-11-15T00:00:00Z",
          },
          201,
        );
      }
      if (
        path.endsWith("/email-connections/hr-mail/revisions?limit=25") &&
        method === "GET"
      ) {
        return response({
          items: [
            {
              scope: {
                organization_id: "local-demo",
                environment_id: "dev",
              },
              connection_id: "hr-mail",
              connection_revision: "v1",
              revision: 1,
              connector_type: "email",
              connector_version: "1.0.0",
              transport: "smtp_tls",
              definition_digest: digest,
              has_secret_reference: true,
              actor_id: "local",
              created_at: "2026-11-15T00:00:00Z",
            },
          ],
          next_before_revision: null,
        });
      }
      if (
        path.endsWith("/email-connections/hr-mail/revisions/v1") &&
        method === "GET"
      ) {
        return response({
          scope: {
            organization_id: "local-demo",
            environment_id: "dev",
          },
          connection_id: "hr-mail",
          connection_revision: "v1",
          revision: 1,
          connector_type: "email",
          connector_version: "1.0.0",
          transport: "smtp_tls",
          definition_digest: digest,
          has_secret_reference: true,
          actor_id: "local",
          created_at: "2026-11-15T00:00:00Z",
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

    const emailWrite = createEmailConnectionWrite(
      "SMTP.Private.example.test",
      "Onboard@EXAMPLE.TEST",
      ["EXAMPLE.TEST"],
      { secretReference: "opaque-private-reference" },
    );
    const emailSaved = await client.putEmailConnection("hr-mail", "v1", emailWrite);
    expect(emailSaved.connector_type).toBe("email");
    expect(emailSaved.transport).toBe("smtp_tls");
    expect(JSON.stringify(emailSaved)).not.toContain("smtp.private");
    expect(JSON.stringify(emailSaved)).not.toContain("Onboard@");
    const emailRevisions = await client.listEmailConnectionRevisions("hr-mail");
    expect(emailRevisions.items[0]?.connection_revision).toBe("v1");
    const emailGet = await client.getEmailConnection("hr-mail", "v1");
    expect(emailGet.has_secret_reference).toBe(true);
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
