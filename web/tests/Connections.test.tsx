import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Connections from "../src/Connections";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const digest =
  "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const connectionMeta = {
  scope: { organization_id: "local-demo", environment_id: "dev" },
  connection_id: "hr-api",
  connection_revision: "v1",
  revision: 1,
  connector_type: "http",
  connector_version: "1.0.0",
  definition_digest: digest,
  has_secret_reference: false,
  actor_id: "local",
  created_at: "2026-11-15T00:00:00Z",
};

const fixtureMeta = {
  scope: { organization_id: "local-demo", environment_id: "dev" },
  connection_id: "hr-api",
  connection_revision: "v1",
  fixture_digest: digest,
  stored_bytes: 24,
};

const emailMeta = {
  scope: { organization_id: "local-demo", environment_id: "dev" },
  connection_id: "hr-mail",
  connection_revision: "v1",
  revision: 1,
  connector_type: "email",
  connector_version: "1.0.0",
  transport: "smtp_tls",
  definition_digest: digest,
  has_secret_reference: false,
  actor_id: "local",
  created_at: "2026-11-15T00:00:00Z",
};

describe("Connections", () => {
  it("registers an HTTP connection and saves a Dev mock fixture", async () => {
    const user = userEvent.setup();
    let putConnectionBody: unknown = null;
    let putFixtureBody: unknown = null;
    let putEmailBody: unknown = null;
    const fetcher = vi.fn<typeof fetch>(async (url, init) => {
      const path = String(url);
      const method = init?.method ?? "GET";
      expect(path).not.toContain("local-token");
      if (path.includes("/mock-fixtures?") && method === "GET") {
        return response({
          items: putFixtureBody ? [fixtureMeta] : [],
          next_after_connection_id: null,
          next_after_connection_revision: null,
        });
      }
      if (
        path.endsWith("/connections/hr-api/revisions?limit=25") &&
        method === "GET"
      ) {
        return response({
          items: putConnectionBody ? [connectionMeta] : [],
          next_before_revision: null,
        });
      }
      if (
        path.endsWith("/email-connections/hr-mail/revisions?limit=25") &&
        method === "GET"
      ) {
        return response({
          items: putEmailBody ? [emailMeta] : [],
          next_before_revision: null,
        });
      }
      if (
        path.endsWith("/connections/hr-api/revisions/v1") &&
        method === "PUT"
      ) {
        putConnectionBody = JSON.parse(String(init?.body));
        return response(connectionMeta, 201);
      }
      if (
        path.endsWith("/email-connections/hr-mail/revisions/v1") &&
        method === "PUT"
      ) {
        putEmailBody = JSON.parse(String(init?.body));
        return response(emailMeta, 201);
      }
      if (
        path.endsWith("/connections/hr-api/revisions/v1/mock-fixture") &&
        method === "PUT"
      ) {
        putFixtureBody = JSON.parse(String(init?.body));
        return response(fixtureMeta, 201);
      }
      if (
        path.endsWith("/connections/hr-api/revisions/v1") &&
        method === "GET"
      ) {
        return response(connectionMeta);
      }
      if (
        path.endsWith("/email-connections/hr-mail/revisions/v1") &&
        method === "GET"
      ) {
        return response(emailMeta);
      }
      if (
        path.endsWith("/connections/hr-api/revisions/v1/mock-fixture") &&
        method === "GET"
      ) {
        return response(fixtureMeta);
      }
      throw new Error(`unexpected ${method} ${path}`);
    });
    vi.stubGlobal("fetch", fetcher);

    render(
      <Connections
        onClose={() => undefined}
        connection={{
          organizationId: "local-demo",
          environmentId: "dev",
          token: "local-token",
        }}
      />,
    );

    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Register HTTP connection" }),
      ).toBeVisible(),
    );
    expect(
      screen.getByRole("heading", { name: "Register email SMTP connection" }),
    ).toBeVisible();
    expect(
      screen.getAllByText(/not live SMTP/i).length,
    ).toBeGreaterThan(0);

    await user.clear(screen.getByLabelText("Base origin"));
    await user.type(
      screen.getByLabelText("Base origin"),
      "https://api.example.com",
    );
    await user.click(
      screen.getByRole("button", { name: "Save connection revision" }),
    );

    await waitFor(() => expect(putConnectionBody).not.toBeNull());
    expect(putConnectionBody).toMatchObject({
      connector_type: "http",
      base_origin: "https://api.example.com",
    });
    await waitFor(() =>
      expect(screen.getByText(/Saved HTTP connection/)).toBeVisible(),
    );
    expect(
      screen.getByText((text) => text.includes(digest)),
    ).toBeVisible();

    await user.clear(screen.getByLabelText("SMTP host"));
    await user.type(screen.getByLabelText("SMTP host"), "smtp.example.test");
    await user.clear(screen.getByLabelText("Sender"));
    await user.type(screen.getByLabelText("Sender"), "noreply@example.test");
    await user.click(
      screen.getByRole("button", { name: "Save email connection revision" }),
    );
    await waitFor(() => expect(putEmailBody).not.toBeNull());
    expect(putEmailBody).toMatchObject({
      connector_type: "email",
      transport: "smtp_tls",
      smtp_host: "smtp.example.test",
      sender: "noreply@example.test",
    });
    await waitFor(() =>
      expect(screen.getByText(/Saved email SMTP connection/)).toBeVisible(),
    );

    await user.click(
      screen.getByRole("button", { name: "Save Dev mock fixture" }),
    );

    await waitFor(() => expect(putFixtureBody).not.toBeNull());
    expect(putFixtureBody).toMatchObject({
      method: "GET",
      path: "/health",
      status_code: 200,
    });
    await waitFor(() =>
      expect(screen.getByText(/Saved Dev HTTP mock/)).toBeVisible(),
    );
    const fixturePanel = screen.getByRole("heading", {
      name: "Dev mock fixtures",
    }).parentElement;
    expect(fixturePanel).not.toBeNull();
    expect(
      within(fixturePanel as HTMLElement).getByRole("button", {
        name: /hr-api \/ v1/,
      }),
    ).toBeVisible();
  });
});
