import { chromium, expect } from "@playwright/test";

const url = process.env.NEXUSFLOW_E2E_URL;
const token = process.env.NEXUSFLOW_E2E_TOKEN;
if (!url || !token)
  throw new Error("Explicit local acceptance configuration required");
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(url);
  await page.getByRole("button", { name: "Projects", exact: true }).click();
  await page.getByLabel("Organization ID", { exact: true }).fill("acme");
  await page.getByLabel("Environment ID", { exact: true }).fill("dev");
  await page.getByLabel("Access token", { exact: true }).fill(token);
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await page
    .getByRole("button", { name: "Open browser-project", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Select approval", exact: true })
    .click();
  await page.getByLabel("Step name", { exact: true }).fill("Finance review");
  await page
    .getByLabel("Description", { exact: true })
    .fill("Review the request");
  await page
    .getByRole("button", { name: "Apply changes", exact: true })
    .click();
  await page.getByRole("button", { name: "Save project", exact: true }).click();
  await expect(page.getByText("Revision 2", { exact: true })).toBeVisible();
  const path =
    url +
    "/api/v1/organizations/acme/environments/dev/projects/browser-project";
  const headers = {
    Authorization: "Bearer " + token,
    "Content-Type": "application/json",
  };
  const latestResponse = await fetch(path, { headers });
  if (!latestResponse.ok) throw new Error("External revision read failed");
  const latest = await latestResponse.json();
  const external = await fetch(path, {
    method: "PUT",
    headers,
    body: JSON.stringify({
      expected_revision: latest.revision,
      summary: "Concurrent author update",
      content: latest.content,
    }),
  });
  if (!external.ok) throw new Error("External revision write failed");
  await page
    .getByLabel("Step name", { exact: true })
    .fill("Unsaved conflicting edit");
  await page
    .getByRole("button", { name: "Apply changes", exact: true })
    .click();
  await page.getByRole("button", { name: "Save project", exact: true }).click();
  await expect(page.getByText(/Save conflict/)).toBeVisible();
  await expect(page.getByLabel("Step name", { exact: true })).toHaveValue(
    "Unsaved conflicting edit",
  );
  await page
    .getByRole("button", { name: "Reload latest", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Discard changes", exact: true })
    .click();
  await expect(page.getByText("Revision 3", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Select Finance review", exact: true }),
  ).toBeVisible();
  if (
    (await page.evaluate(() => localStorage.length + sessionStorage.length)) !==
    0
  )
    throw new Error("Unexpected browser persistence");
} finally {
  await browser.close();
}
