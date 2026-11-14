import { expect, test } from "@playwright/test";

// Fake-HTTP browser acceptance for mountable ReleaseControls. Not Entra/API evidence.
// Coordinator wires this into App/Projects; this harness mounts the export directly.

const TOKEN = "browser-only-release-token";
const digest =
  "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

test("publish activate start status critical path through mocked HTTP", async ({
  page,
}, testInfo) => {
  const bodies: unknown[] = [];

  await page.addInitScript((token) => {
    (
      window as Window & {
        __NEXUS_RELEASE_CONTROLS_E2E__?: Record<string, unknown>;
      }
    ).__NEXUS_RELEASE_CONTROLS_E2E__ = {
      connection: {
        organizationId: "acme",
        environmentId: "dev",
        token,
      },
      projectId: "hr",
      headRevision: 2,
      workflowId: "onboarding",
      validationOk: true,
      configurationRevision: "local-v1",
      pollIntervalMs: 250,
    };
  }, TOKEN);

  await page.route("**/", async (route) => {
    if (route.request().resourceType() !== "document") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Release controls harness</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/ReleaseControls.tsx"></script>
  </body>
</html>`,
    });
  });

  await page.route(
    "**/api/v1/organizations/acme/environments/dev/projects/hr/**",
    async (route) => {
      const request = route.request();
      expect(request.headers().authorization).toBe(`Bearer ${TOKEN}`);
      const url = request.url();
      if (request.method() === "GET" && url.endsWith("/activation")) {
        await route.fulfill({
          json: { revision: 0, release_id: null },
        });
        return;
      }
      if (request.method() === "POST" && url.endsWith("/releases")) {
        bodies.push(request.postDataJSON());
        await route.fulfill({
          status: 201,
          json: {
            project_id: "hr",
            release_id: "rel-e2e",
            source_revision: 2,
            checksum: digest,
          },
        });
        return;
      }
      if (request.method() === "POST" && url.endsWith("/activation")) {
        bodies.push(request.postDataJSON());
        await route.fulfill({
          json: {
            revision: 1,
            release_id: "rel-e2e",
            project_id: "hr",
            operation_id: request.postDataJSON().operation_id,
            previous_release_id: null,
            runtime_version: "proof.v1",
            configuration_revision: "local-v1",
            checksum: digest,
            actor_id: "alice",
            created_at: "2026-11-14T11:00:00+00:00",
          },
        });
        return;
      }
      if (request.method() === "POST" && url.endsWith("/executions")) {
        bodies.push(request.postDataJSON());
        await route.fulfill({
          status: 202,
          json: {
            execution_id: "run-e2e",
            release_id: "rel-e2e",
            configuration_revision: "local-v1",
            state: "accepted",
          },
        });
        return;
      }
      if (request.method() === "GET" && url.includes("/executions/run-e2e")) {
        await route.fulfill({
          json: {
            release_id: "rel-e2e",
            definition_digest: digest,
            runtime_version: "proof.v1",
            node_id: "review",
            state: "completed",
            revision: 2,
            completed_nodes: ["start", "review"],
          },
        });
        return;
      }
      await route.fulfill({ status: 404, json: { code: "not_found" } });
    },
  );

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Publish and start", exact: true }),
  ).toBeVisible();

  await page.getByRole("button", { name: "Publish release", exact: true }).click();
  await expect(page.getByRole("definition").filter({ hasText: "rel-e2e" })).toBeVisible();
  await page.getByRole("button", { name: "Activate", exact: true }).click();
  await expect(page.getByText("rev 1 · rel-e2e", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start execution", exact: true }).click();
  await expect(page.getByText("run-e2e", { exact: true })).toBeVisible();
  await expect(page.getByText(/completed · node review/)).toBeVisible();
  await expect(page.getByText("run_id=run-e2e", { exact: true })).toBeVisible();

  expect(bodies).toHaveLength(3);
  expect((bodies[0] as { source_revision: number }).source_revision).toBe(2);
  expect(
    String((bodies[0] as { idempotency_key: string }).idempotency_key),
  ).toMatch(/^pub-/);
  expect(bodies[1]).toMatchObject({
    project_id: "hr",
    release_id: "rel-e2e",
    configuration_revision: "local-v1",
    expected_revision: 0,
    runtime_version: "proof.v1",
    action: "activate",
  });
  expect(bodies[2]).toMatchObject({
    workflow_id: "onboarding",
    input: {},
    source: "manual",
  });

  await page.screenshot({
    path: testInfo.outputPath("release-controls.png"),
    fullPage: true,
  });
});

test("validation gate blocks publish in the browser harness", async ({
  page,
}) => {
  await page.addInitScript((token) => {
    (
      window as Window & {
        __NEXUS_RELEASE_CONTROLS_E2E__?: Record<string, unknown>;
      }
    ).__NEXUS_RELEASE_CONTROLS_E2E__ = {
      connection: {
        organizationId: "acme",
        environmentId: "dev",
        token,
      },
      projectId: "hr",
      headRevision: 2,
      workflowId: "onboarding",
      validationOk: false,
    };
  }, TOKEN);

  await page.route("**/", async (route) => {
    if (route.request().resourceType() !== "document") {
      await route.continue();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `<!doctype html>
<html lang="en">
  <body>
    <div id="root"></div>
    <script type="module" src="/src/ReleaseControls.tsx"></script>
  </body>
</html>`,
    });
  });

  const releases: string[] = [];
  await page.route(
    "**/api/v1/organizations/acme/environments/dev/projects/hr/**",
    async (route) => {
      const request = route.request();
      if (request.method() === "GET" && request.url().endsWith("/activation")) {
        await route.fulfill({ json: { revision: 0, release_id: null } });
        return;
      }
      if (request.url().includes("/releases")) {
        releases.push(request.url());
        await route.fulfill({ status: 500, json: { code: "unavailable" } });
        return;
      }
      await route.fulfill({ status: 404, json: { code: "not_found" } });
    },
  );

  await page.goto("/");
  await expect(
    page.getByText("Publish is blocked until Designer validation passes"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Publish release", exact: true }),
  ).toBeDisabled();
  expect(releases).toHaveLength(0);
});
