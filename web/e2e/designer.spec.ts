import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

test("business author edits, undoes, inserts and reloads a sample without a backend", async ({
  page,
}) => {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) requests.push(request.url());
  });
  await page.goto("/");
  await expect(
    page.getByText("Sample project · changes stay in this tab"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Test workflow", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "Export project", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "Select Manager approval", exact: true })
    .click();
  await page
    .getByRole("textbox", { name: "Step name", exact: true })
    .fill("Finance review");
  await page
    .getByRole("button", { name: "Apply changes", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Select Finance review", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Select Manager approval", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Redo", exact: true }).click();
  await page.getByRole("button", { name: "Activities", exact: true }).click();
  await page
    .getByRole("searchbox", { name: "Search activities", exact: true })
    .fill("timer");
  await page.getByRole("button", { name: "Add timer", exact: true }).click();
  await expect(page.getByRole("button", { name: /^Select / })).toHaveCount(8);
  await expect(
    page.getByText("Approved", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("Rejected", { exact: true }).first(),
  ).toBeVisible();
  page.on("dialog", (dialog) => dialog.accept());
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Select Manager approval", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: /^Select / })).toHaveCount(7);
  expect(requests).toEqual([]);
});

test("keyboard selection and literal text preserve safe authoring", async ({
  page,
}) => {
  const approval = page.getByRole("button", {
    name: "Select Manager approval",
    exact: true,
  });
  await approval.focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("textbox", { name: "Step name", exact: true }),
  ).toHaveValue("Manager approval");
  const before = await approval.boundingBox();
  await approval.press("ArrowRight");
  const after = await approval.boundingBox();
  expect(before).not.toBeNull();
  expect(after).not.toBeNull();
  expect(after!.x).toBeGreaterThan(before!.x);
  const literal = "<img src=x onerror=alert(1)>";
  await page
    .getByRole("textbox", { name: "Step name", exact: true })
    .fill(literal);
  await page
    .getByRole("button", { name: "Apply changes", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: `Select ${literal}`, exact: true }),
  ).toBeVisible();
  await expect(page.locator('img[src="x"], [onerror]')).toHaveCount(0);
  await page
    .getByRole("button", { name: "Close properties", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Workflow outline", exact: true })
    .click();
  await page
    .getByRole("navigation", { name: "Workflow outline", exact: true })
    .getByRole("button", { name: "Select Set request status", exact: true })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Step name", exact: true }),
  ).toHaveValue("Set request status");
});

for (const width of [1600, 1280, 800]) {
  test(`spatial workspace remains operable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page
      .getByRole("button", { name: "Fit workflow", exact: true })
      .click();
    await expect(
      page.getByRole("button", {
        name: "Select Approval outcome",
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Zoom in", exact: true }).click();
    await page.getByRole("button", { name: "Zoom out", exact: true }).click();
    await page
      .getByRole("button", { name: "Fit workflow", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Select End", exact: true }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const geometry = await page
      .locator(".canvas-node img")
      .evaluateAll((images) => {
        const transform = getComputedStyle(
          document.querySelector(".canvas-world")!,
        ).transform;
        const scale =
          transform === "none" ? 1 : new DOMMatrixReadOnly(transform).a;
        return images.map((element) => {
          const image = element as HTMLImageElement;
          const rect = image.getBoundingClientRect();
          return {
            loaded:
              image.complete &&
              image.naturalWidth > 0 &&
              image.naturalHeight > 0,
            naturalWidth: image.naturalWidth,
            naturalHeight: image.naturalHeight,
            renderedWidth: rect.width / scale,
            renderedHeight: rect.height / scale,
          };
        });
      });
    expect(geometry).toHaveLength(6);
    for (const image of geometry) {
      expect(image.loaded).toBe(true);
      expect(image.renderedWidth).toBeCloseTo(image.naturalWidth, 2);
      expect(image.renderedHeight).toBeCloseTo(image.naturalHeight, 2);
    }
    // Fit is below the fold on the taller properties panel. Reset document scroll
    // so full-page capture does not relocate offscreen fixed elements into view.
    await page.evaluate(() => window.scrollTo(0, 0));
    const skip = page.getByRole("link", {
      name: "Skip to workflow",
      exact: true,
    });
    const skipGeometry = await skip.boundingBox();
    expect(skipGeometry).not.toBeNull();
    expect(skipGeometry!.y + skipGeometry!.height).toBeLessThanOrEqual(0);
    await expect(skip).not.toBeFocused();
    await page.screenshot({
      path: `../artifacts/react-designer/workspace-${width}.png`,
      fullPage: true,
    });
  });
}
