import { expect, test } from "@playwright/test";

// Fake-HTTP browser acceptance for the mountable Tasks surface.
// App.tsx composition is coordinator-owned; this harness mounts Tasks via Vite.
const TOKEN = "browser-only-private-token";
const BASE = "/api/v1/organizations/local-demo/environments/dev/tasks";

function task(overrides: Record<string, unknown> = {}) {
  return {
    task_id: "task-1",
    scope: { organization_id: "local-demo", environment_id: "dev" },
    request: {
      run_id: "run-42",
      node_invocation_id: "manager",
      definition_revision: "r1",
      rule: "any_one",
      user_ids: ["user-1"],
      group_ids: [],
    },
    eligible_approver_ids: ["user-1"],
    status: "pending",
    revision: 1,
    decisions: [],
    assignment_history: [],
    delegation_history: [],
    stage_assignments: [],
    stage_index: 0,
    created_at: "2026-11-14T09:00:00+00:00",
    ...overrides,
  };
}

function form(revision = 1) {
  return {
    revision,
    fields: [
      {
        field_id: "confirmed",
        label: "Confirm request",
        schema_json: '{"type":"boolean","const":true}',
        editable: true,
        required: true,
        write_only: false,
      },
      {
        field_id: "employee_name",
        label: "Employee name",
        schema_json: '{"type":"string"}',
        editable: false,
        required: false,
        write_only: false,
      },
    ],
    values_json: '{"employee_name":"Ada"}',
  };
}

async function mountPortal(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.evaluate(async () => {
    document.body.innerHTML = '<div id="tasks-root"></div>';
    const mod = await import("/src/Tasks.tsx");
    await mod.mountTasks("tasks-root");
  });
  await page.getByRole("heading", { name: "Task portal" }).waitFor();
}

test("mock HTTP inbox, form submit, and approve decision", async ({
  page,
}, testInfo) => {
  let revision = 1;
  let approved = false;
  const decisionBodies: unknown[] = [];
  await page.route(`**${BASE}**`, async (route) => {
    const request = route.request();
    expect(request.headers().authorization).toBe(`Bearer ${TOKEN}`);
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "GET" && path.endsWith("/tasks")) {
      await route.fulfill({
        json: {
          items: approved ? [] : [task({ revision })],
          next_offset: null,
        },
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/task-1")) {
      await route.fulfill({
        json: task({
          revision,
          status: approved ? "approved" : "pending",
        }),
      });
      return;
    }
    if (request.method() === "GET" && path.endsWith("/form")) {
      await route.fulfill({ json: form(revision) });
      return;
    }
    if (request.method() === "POST" && path.endsWith("/form")) {
      const body = request.postDataJSON();
      expect(body.expected_revision).toBe(revision);
      expect(body.values.confirmed).toBe(true);
      revision += 1;
      await route.fulfill({ json: { revision } });
      return;
    }
    if (request.method() === "POST" && path.endsWith("/decisions")) {
      const body = request.postDataJSON();
      decisionBodies.push(body);
      expect(body.expected_revision).toBe(revision);
      expect(body.decision).toBe("approve");
      expect(body.command_id).toBeTruthy();
      approved = true;
      revision += 1;
      await route.fulfill({
        json: task({ revision, status: "approved" }),
      });
      return;
    }
    await route.fulfill({ status: 404, json: { code: "not_found" } });
  });

  await mountPortal(page);
  await page.getByLabel("Organization ID", { exact: true }).fill("local-demo");
  await page.getByLabel("Environment ID", { exact: true }).fill("dev");
  await page.getByLabel("Access token", { exact: true }).fill(TOKEN);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await page.getByLabel("Filter by run ID", { exact: true }).fill("run-42");
  await page
    .getByRole("button", { name: "Refresh inbox", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Open task task-1", exact: true })
    .click();
  await expect(page.getByText("Ada", { exact: true })).toBeVisible();
  await page.getByLabel(/Confirm request/).check();
  await page.getByRole("button", { name: "Submit form", exact: true }).click();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByText(/Task approved/i)).toBeVisible();
  expect(decisionBodies).toHaveLength(1);
  expect(page.url()).not.toContain(TOKEN);
  for (const width of [1280, 800]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      )
      .toBe(true);
    await page.screenshot({
      path: testInfo.outputPath(`tasks-${width}.png`),
      fullPage: true,
    });
  }
});
