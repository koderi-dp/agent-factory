import { test, expect } from "./fixtures";
import AxeBuilder from "@axe-core/playwright";
import { fileURLToPath } from "node:url";

test("backlog, live board and nodes, activity, Stop/Resume, reports and keyboard", async ({
  page,
  engine,
}) => {
  const repo = engine.repos[0];
  await page.goto(`${engine.url}/#/repo/${repo}/backlog`);
  await page.getByLabel("Title", { exact: true }).fill("Ship the feature");
  await page.getByRole("button", { name: "Add item" }).click();
  await page.getByRole("button", { name: "Start", exact: true }).click();
  await page.getByRole("link", { name: "Board", exact: true }).click();
  const build = page.getByRole("region", { name: "build", exact: true });
  await expect(build.getByRole("link", { name: "Ship the feature" })).toBeVisible();
  await expect(build).toContainText("Reading request");
  await expect(build).toContainText("working č");
  await page.getByRole("link", { name: "Ship the feature" }).click();
  const nodes = page.getByRole("list", { name: "Pipeline executions" }).getByRole("button");
  await expect(nodes.first()).toHaveAccessibleName(/running|valid|READY/);
  await nodes.first().focus();
  await page.keyboard.press("ArrowRight");
  await expect(nodes.nth(1)).toBeFocused();
  await nodes.first().click();
  await expect(page.getByRole("dialog")).toContainText("build/build");
  await expect(
    page.getByRole("dialog").getByRole("tab", { name: "Log", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
  const jobId = new URL(page.url()).hash.split("/").at(-1);
  await expect
    .poll(async () => {
      const response = await page.request.get(`${engine.url}/api/repos/${repo}/jobs/${jobId}`);
      const detail = await response.json();
      return detail.attempts.some((a: { state: string }) =>
        ["claimed", "running", "stopping"].includes(a.state),
      );
    })
    .toBe(false);
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Resume", exact: true }).click();
  await expect(nodes.nth(1)).toHaveAccessibleName(/running/);
  await page.getByRole("link", { name: "Board", exact: true }).click();
  await expect(
    page
      .getByRole("region", { name: "approve", exact: true })
      .getByRole("link", { name: "Ship the feature" }),
  ).toBeVisible();
  await expect(build.getByRole("link", { name: "Ship the feature" })).toHaveCount(0);
  await page.getByRole("link", { name: "Ship the feature" }).click();
  await expect(nodes.nth(1)).toHaveAccessibleName(/APPROVE|valid/, { timeout: 25_000 });
  await nodes.nth(1).click();
  await expect(page.getByRole("dialog")).toContainText("approve/approve");
  await expect(
    page.getByRole("dialog").getByRole("tab", { name: "Report", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("dialog")).toContainText("APPROVE");
});

test("unsaved provider profiles survive SSE and JSON preview validates steps", async ({
  page,
  engine,
}) => {
  const repo = engine.repos[0];
  await page.addInitScript(() => {
    window.EventSource = new Proxy(window.EventSource, {
      construct(Target, args) {
        const source = new Target(...(args as [string, EventSourceInit?]));
        source.addEventListener("backlog", () => {
          document.documentElement.dataset.backlogEvent = "received";
        });
        return source;
      },
    });
  });
  await page.goto(`${engine.url}/#/repo/${repo}/settings`);
  await page.getByLabel("build model", { exact: true }).fill("unsaved-model");
  await page.getByLabel("build provider", { exact: true }).selectOption("claude");
  await expect(page.getByLabel("build model", { exact: true })).toHaveValue("saved-profile");
  // A real durable event refreshes the settings snapshot while the form is dirty.
  await page.request.post(`${engine.url}/api/repos/${repo}/backlog`, {
    headers: { "X-Factory-Token": "e2e-token" },
    data: { action: "add", text: "SSE refresh" },
  });
  await expect(page.locator("html")).toHaveAttribute("data-backlog-event", "received");
  await page.getByLabel("build provider", { exact: true }).selectOption("codex");
  await expect(page.getByLabel("build model", { exact: true })).toHaveValue("unsaved-model");
  const editor = page.getByRole("textbox", { name: /JSON/ });
  const config = JSON.parse(await editor.inputValue());
  config.pipelines.test[0].id = "renamed";
  await editor.fill(JSON.stringify(config, null, 2));
  await expect(
    page.getByRole("list", { name: "Pipeline executions" }).getByRole("button").first(),
  ).toHaveAccessibleName(/renamed/);
  config.pipelines.test[0].uses = "missing-role";
  await editor.fill(JSON.stringify(config, null, 2));
  await expect(page.locator(".pipeline-invalid")).toHaveCount(1);
  await expect(page.getByRole("alert")).toContainText(/missing-role/);
});

test("repo switcher, overview counts, server death and SSE reconnect", async ({ page, engine }) => {
  await page.goto(engine.url);
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await expect(page.getByText("0 running", { exact: true })).toHaveCount(2);
  await page.getByRole("combobox").first().selectOption(engine.repos[1]);
  await expect(page.getByRole("heading", { name: "beta", exact: true })).toBeVisible();
  await engine.stop();
  await expect(page.locator("header").getByRole("status")).toContainText("Disconnected");
  await engine.restart();
  await expect(page.locator("header").getByRole("status")).toContainText("Connected", {
    timeout: 25_000,
  });
  await page.request.post(`${engine.url}/api/repos/${engine.repos[1]}/jobs`, {
    headers: { "X-Factory-Token": "e2e-token" },
    data: { title: "After reconnect" },
  });
  await expect(page.getByRole("link", { name: "After reconnect" })).toBeVisible();
  await page.getByRole("link", { name: "After reconnect" }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Stop", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(page.getByText("1 need you", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("combobox").first().locator("option", { hasText: "beta" }),
  ).toContainText("1 need you");
});

test("dark screenshot baselines and axe contrast", async ({ page, engine }) => {
  await page.goto(engine.url);
  await expect(page.getByRole("heading", { name: "Overview", exact: true })).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  const screenshotStyle = fileURLToPath(new URL("./screenshot.css", import.meta.url));
  async function capture(name: string) {
    await expect(page.locator("header").getByRole("status")).toContainText("Connected");
    const result = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
    expect(result.violations).toEqual([]);
    await expect(page).toHaveScreenshot(`${name}.png`, {
      animations: "disabled",
      stylePath: screenshotStyle,
      mask: [page.getByText(/in stage$/)],
    });
  }
  await capture("overview");
  await page.getByRole("combobox").first().selectOption(engine.repos[0]);
  await expect(page.getByRole("heading", { name: "alpha", exact: true })).toBeVisible();
  const response = await page.request.post(`${engine.url}/api/repos/${engine.repos[0]}/jobs`, {
    headers: { "X-Factory-Token": "e2e-token" },
    data: { title: "Visual fixture" },
  });
  const job = await response.json();
  await expect
    .poll(
      async () =>
        page.request
          .get(`${engine.url}/api/repos/${engine.repos[0]}/jobs/${job.id}`)
          .then((r) => r.json())
          .then((d) => d.job.state),
      { timeout: 30_000 },
    )
    .toBe("needs_human");
  await expect(page.getByText("Needs you", { exact: true })).toBeVisible();
  await capture("board");
  await page.getByRole("link", { name: "Visual fixture" }).click();
  await expect(page.getByRole("button", { name: "Merge", exact: true })).toBeVisible();
  await capture("job-detail");
  await expect(page.getByRole("list", { name: "Pipeline executions" })).toHaveScreenshot(
    "pipeline.png",
    { animations: "disabled", stylePath: screenshotStyle },
  );
});
