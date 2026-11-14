import { expect, test } from "@playwright/test";
import {
  projectList,
  projectRevision,
  saveAcknowledgement,
  templateList,
  templateProjectRevision,
} from "../tests/project-fixtures";

// These routes are independent fake HTTP browser acceptance, not Entra/API evidence.
const TOKEN = "browser-only-private-token";
async function mockDevApi(page: import("@playwright/test").Page) {
  const writes: any[] = [];
  const urls: string[] = [];
  await page.route(
    "**/api/v1/organizations/acme/environments/dev/**",
    async (route) => {
      const request = route.request();
      const url = request.url();
      urls.push(url);
      expect(request.headers().authorization).toBe(`Bearer ${TOKEN}`);
      if (url.includes("/project-templates/") && url.endsWith("/projects")) {
        const body = request.postDataJSON();
        await route.fulfill({
          status: 201,
          json: templateProjectRevision(body.project_id, body.name),
        });
        return;
      }
      if (url.includes("/project-templates")) {
        await route.fulfill({ json: templateList() });
        return;
      }
      if (url.includes("/validation")) {
        await route.fulfill({ json: [] });
        return;
      }
      if (request.method() === "PUT") {
        const body = request.postDataJSON();
        writes.push(body);
        const projectId = url.replace(/\/$/, "").split("/").pop()!;
        await route.fulfill({
          json: { ...saveAcknowledgement(body), project_id: projectId },
        });
        return;
      }
      if (url.includes("/projects?")) {
        await route.fulfill({ json: projectList() });
        return;
      }
      await route.fulfill({ json: projectRevision() });
    },
  );
  return { writes, urls };
}
async function enterProjects(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Projects", exact: true }).click();
  await page.getByLabel("Organization ID", { exact: true }).fill("acme");
  await page.getByLabel("Environment ID", { exact: true }).fill("dev");
  await page.getByLabel("Access token", { exact: true }).fill(TOKEN);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await page.getByRole("button", { name: "Open hr", exact: true }).click();
  await page
    .getByRole("button", { name: "Select Manager review", exact: true })
    .first()
    .click();
}

test("mock HTTP project load and acknowledged full-content layout save", async ({
  page,
}, testInfo) => {
  const { writes, urls } = await mockDevApi(page);
  await enterProjects(page);
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
      path: testInfo.outputPath(`projects-${width}.png`),
      fullPage: true,
    });
  }
  await page
    .getByRole("textbox", { name: "Step name", exact: true })
    .fill("Finance review");
  await page
    .getByRole("button", { name: "Apply changes", exact: true })
    .click();
  await page.getByRole("button", { name: "Save project", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save project", exact: true }),
  ).toBeDisabled();
  expect(writes).toHaveLength(1);
  expect(writes[0].expected_revision).toBe(7);
  expect(writes[0].content.workflows).toEqual(
    projectRevision().content.workflows,
  );
  expect(writes[0].content.forms).toEqual(projectRevision().content.forms);
  expect(
    writes[0].content.layout.nexus_flow_designer.workflows.onboarding.nodes
      .review.label,
  ).toBe("Finance review");
  expect(urls.every((url) => !url.includes(TOKEN))).toBe(true);
  expect(page.url()).not.toContain(TOKEN);
  expect(
    await page.evaluate(() =>
      JSON.stringify({
        local: { ...localStorage },
        session: { ...sessionStorage },
      }),
    ),
  ).not.toContain(TOKEN);
  await expect(page.getByText(TOKEN, { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(page.getByLabel("Access token", { exact: true })).toHaveValue(
    "",
  );
});

test("mock HTTP conflict keeps local edits until explicitly discarded", async ({
  page,
}) => {
  let reads = 0;
  await page.route(
    "**/api/v1/organizations/acme/environments/dev/**",
    async (route) => {
      const request = route.request();
      const url = request.url();
      if (url.includes("/project-templates")) {
        await route.fulfill({ json: templateList() });
        return;
      }
      if (request.method() === "PUT")
        await route.fulfill({ status: 409, json: { message: TOKEN } });
      else if (url.includes("/projects?"))
        await route.fulfill({ json: projectList() });
      else {
        reads++;
        await route.fulfill({ json: projectRevision() });
      }
    },
  );
  await enterProjects(page);
  await page
    .getByRole("textbox", { name: "Step name", exact: true })
    .fill("Local approval");
  await page
    .getByRole("button", { name: "Apply changes", exact: true })
    .click();
  await page.getByRole("button", { name: "Save project", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText(
    /conflict|another.*revision|changed.*since/i,
  );
  await expect(page.getByRole("textbox", { name: "Step name" })).toHaveValue(
    "Local approval",
  );
  await page
    .getByRole("button", { name: "Reload latest", exact: true })
    .click();
  await expect(
    page.getByRole("alertdialog", { name: "Discard unsaved changes?" }),
  ).toBeVisible();
  expect(reads).toBe(1);
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Step name" })).toHaveValue(
    "Local approval",
  );
  await page
    .getByRole("button", { name: "Reload latest", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(
    page
      .getByRole("button", { name: "Select Manager review", exact: true })
      .first(),
  ).toBeVisible();
  expect(reads).toBe(2);
  await expect(page.getByText(TOKEN, { exact: true })).toHaveCount(0);
});

test("mock HTTP template create, wait insert, and executable save", async ({
  page,
}) => {
  const { writes } = await mockDevApi(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Projects", exact: true }).click();
  await page.getByLabel("Organization ID", { exact: true }).fill("acme");
  await page.getByLabel("Environment ID", { exact: true }).fill("dev");
  await page.getByLabel("Access token", { exact: true }).fill(TOKEN);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await page.getByLabel("Project ID", { exact: true }).fill("people");
  await page
    .getByLabel("Project name", { exact: true })
    .fill("People onboarding");
  for (const slot of ["manager", "hr", "it"]) {
    await page
      .getByLabel(`${slot} assignee user ID`, { exact: true })
      .fill("manager-user");
  }
  await page.getByRole("button", { name: "Create draft", exact: true }).click();
  await expect(
    page.getByText(/Created Employee onboarding draft at revision 1/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Select manager", exact: true })
    .first()
    .click();
  await page.getByRole("button", { name: "Insert wait", exact: true }).click();
  await page.getByRole("button", { name: "Save project", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Save project", exact: true }),
  ).toBeDisabled();
  expect(writes).toHaveLength(1);
  expect(writes[0].expected_revision).toBe(1);
  expect(writes[0].summary).toBe("Update workflow steps");
  expect(
    writes[0].content.workflows[0].nodes.some(
      (node: { kind: string }) => node.kind === "wait",
    ),
  ).toBe(true);
});
