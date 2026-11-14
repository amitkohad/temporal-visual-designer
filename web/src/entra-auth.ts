/** Optional Entra SPA sign-in for Hosted Dev pilots. Local-demo token paste remains default. */

export type BrowserAuthConfig = {
  enabled: false;
} | {
  enabled: true;
  organizationId: string;
  environmentId: string;
  tenantId: string;
  spaClientId: string;
  authority: string;
  scopes: string[];
  redirectUri: string;
};

type PublicBrowserAuth = {
  enabled?: unknown;
  organization_id?: unknown;
  environment_id?: unknown;
  tenant_id?: unknown;
  spa_client_id?: unknown;
  authority?: unknown;
  scopes?: unknown;
  redirect_uri?: unknown;
};

function isIdentifier(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_.-]{1,128}$/.test(value);
}

function isUuid(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    ) &&
    value === value.toLowerCase()
  );
}

export function parseBrowserAuthConfig(value: unknown): BrowserAuthConfig {
  if (!value || typeof value !== "object") return { enabled: false };
  const body = value as PublicBrowserAuth;
  if (body.enabled !== true) return { enabled: false };
  if (
    !isIdentifier(body.organization_id) ||
    !isIdentifier(body.environment_id) ||
    !isUuid(body.tenant_id) ||
    !isUuid(body.spa_client_id) ||
    typeof body.authority !== "string" ||
    !body.authority.startsWith("https://login.microsoftonline.com/") ||
    body.authority.length > 256 ||
    !Array.isArray(body.scopes) ||
    body.scopes.length < 1 ||
    body.scopes.length > 8 ||
    body.scopes.some(
      (scope) => typeof scope !== "string" || !scope || scope.length > 512,
    ) ||
    typeof body.redirect_uri !== "string" ||
    !/^https?:\/\/(127\.0\.0\.1|localhost)(?::\d+)?\/?$/i.test(
      body.redirect_uri,
    )
  ) {
    return { enabled: false };
  }
  return {
    enabled: true,
    organizationId: body.organization_id,
    environmentId: body.environment_id,
    tenantId: body.tenant_id,
    spaClientId: body.spa_client_id,
    authority: body.authority,
    scopes: [...body.scopes],
    redirectUri: body.redirect_uri.endsWith("/")
      ? body.redirect_uri
      : `${body.redirect_uri}/`,
  };
}

export async function fetchBrowserAuthConfig(
  fetcher: typeof fetch = fetch,
): Promise<BrowserAuthConfig> {
  try {
    const response = await fetcher("/api/v1/auth/browser", {
      method: "GET",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return { enabled: false };
    return parseBrowserAuthConfig(await response.json());
  } catch {
    return { enabled: false };
  }
}

export type EntraTokenResult = {
  accessToken: string;
  organizationId: string;
  environmentId: string;
};

type TokenCarrier = { accessToken?: string };

type MsalTestDouble = {
  PublicClientApplication: new (config: {
    auth: {
      clientId: string;
      authority: string;
      redirectUri: string;
    };
    cache: { cacheLocation: "sessionStorage" };
  }) => {
    initialize: () => Promise<void>;
    loginPopup: (request: { scopes: string[] }) => Promise<TokenCarrier>;
    acquireTokenSilent: (request: {
      scopes: string[];
      account: object;
    }) => Promise<TokenCarrier>;
    getAllAccounts: () => object[];
  };
};

let msalLoader: (() => Promise<MsalTestDouble>) | null = null;

/** Test seam; production loads @azure/msal-browser dynamically. */
export function setMsalLoaderForTests(
  loader: (() => Promise<MsalTestDouble>) | null,
): void {
  msalLoader = loader;
}

function readAccessToken(result: TokenCarrier): string {
  const accessToken = result.accessToken;
  if (
    typeof accessToken !== "string" ||
    accessToken.length < 1 ||
    accessToken.length > 16384 ||
    !/^[A-Za-z0-9._~+\/-]+=*$/.test(accessToken)
  ) {
    throw new Error("Entra did not return a usable access token");
  }
  return accessToken;
}

export async function acquireEntraAccessToken(
  config: Extract<BrowserAuthConfig, { enabled: true }>,
): Promise<EntraTokenResult> {
  if (msalLoader) {
    const msal = await msalLoader();
    const app = new msal.PublicClientApplication({
      auth: {
        clientId: config.spaClientId,
        authority: config.authority,
        redirectUri: config.redirectUri,
      },
      cache: { cacheLocation: "sessionStorage" },
    });
    await app.initialize();
    const request = { scopes: config.scopes };
    const accounts = app.getAllAccounts();
    let result: TokenCarrier;
    try {
      result = accounts[0]
        ? await app.acquireTokenSilent({ ...request, account: accounts[0] })
        : await app.loginPopup(request);
    } catch {
      result = await app.loginPopup(request);
    }
    return {
      accessToken: readAccessToken(result),
      organizationId: config.organizationId,
      environmentId: config.environmentId,
    };
  }

  const { PublicClientApplication } = await import("@azure/msal-browser");
  const app = new PublicClientApplication({
    auth: {
      clientId: config.spaClientId,
      authority: config.authority,
      redirectUri: config.redirectUri,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await app.initialize();
  const request = { scopes: config.scopes };
  const accounts = app.getAllAccounts();
  let result: TokenCarrier;
  try {
    result = accounts[0]
      ? await app.acquireTokenSilent({ ...request, account: accounts[0] })
      : await app.loginPopup(request);
  } catch {
    result = await app.loginPopup(request);
  }
  return {
    accessToken: readAccessToken(result),
    organizationId: config.organizationId,
    environmentId: config.environmentId,
  };
}
