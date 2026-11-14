import { afterEach, describe, expect, it, vi } from "vitest";
import {
  acquireEntraAccessToken,
  fetchBrowserAuthConfig,
  parseBrowserAuthConfig,
  setMsalLoaderForTests,
} from "./entra-auth";

afterEach(() => {
  setMsalLoaderForTests(null);
  vi.unstubAllGlobals();
});

describe("parseBrowserAuthConfig", () => {
  it("disables malformed or incomplete payloads", () => {
    expect(parseBrowserAuthConfig(null)).toEqual({ enabled: false });
    expect(parseBrowserAuthConfig({ enabled: true })).toEqual({
      enabled: false,
    });
  });

  it("accepts a valid public SPA config", () => {
    expect(
      parseBrowserAuthConfig({
        enabled: true,
        organization_id: "pilot-org",
        environment_id: "dev",
        tenant_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        spa_client_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        authority:
          "https://login.microsoftonline.com/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        scopes: ["api://nexus-flow-pilot/access_as_user"],
        redirect_uri: "http://127.0.0.1:8000/",
      }),
    ).toEqual({
      enabled: true,
      organizationId: "pilot-org",
      environmentId: "dev",
      tenantId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      spaClientId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      authority:
        "https://login.microsoftonline.com/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      scopes: ["api://nexus-flow-pilot/access_as_user"],
      redirectUri: "http://127.0.0.1:8000/",
    });
  });
});

describe("fetchBrowserAuthConfig", () => {
  it("returns disabled when the host has no Entra config", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ enabled: false }),
      ) as unknown as typeof fetch,
    );
    await expect(fetchBrowserAuthConfig()).resolves.toEqual({ enabled: false });
  });

  it("fails soft on transport errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("offline");
      }) as unknown as typeof fetch,
    );
    await expect(fetchBrowserAuthConfig()).resolves.toEqual({ enabled: false });
  });
});

describe("acquireEntraAccessToken", () => {
  it("returns the MSAL access token for the configured org", async () => {
    setMsalLoaderForTests(async () => ({
      PublicClientApplication: class {
        async initialize() {}
        getAllAccounts() {
          return [];
        }
        async loginPopup() {
          return { accessToken: "header.payload.signature" };
        }
        async acquireTokenSilent() {
          return { accessToken: "header.payload.signature" };
        }
      },
    }));
    await expect(
      acquireEntraAccessToken({
        enabled: true,
        organizationId: "pilot-org",
        environmentId: "dev",
        tenantId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        spaClientId: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        authority:
          "https://login.microsoftonline.com/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        scopes: ["api://nexus-flow-pilot/access_as_user"],
        redirectUri: "http://127.0.0.1:8000/",
      }),
    ).resolves.toEqual({
      accessToken: "header.payload.signature",
      organizationId: "pilot-org",
      environmentId: "dev",
    });
  });
});
