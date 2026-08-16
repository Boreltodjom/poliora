/* Browser-level release smoke test for a running Poliora dashboard. */

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const url = process.argv[2] || "http://127.0.0.1:8787";
const outputDirectory = path.resolve(process.argv[3] || "artifacts/ui-smoke");
const exerciseDecision = process.env.POLIORA_UI_WRITE_JOURNEY === "1";
const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));

async function inspectViewport(browser, name, viewport) {
  const page = await browser.newPage({ viewport });
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`page: ${error.message}`));
  page.on("response", (response) => {
    if (response.status() >= 400) errors.push(`http: ${response.status()} ${response.url()}`);
  });

  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 20_000 });
  if (!response || !response.ok()) throw new Error(`${name}: dashboard returned ${response?.status()}`);
  const demoChoice = page.locator('[data-welcome-action="demo"]');
  if (await demoChoice.isVisible()) {
    await demoChoice.click();
  }
  try {
    await page.waitForSelector("#metrics .metric", { timeout: 10_000 });
  } catch {
    throw new Error(`${name}: dashboard metrics did not render; ${errors.join("; ") || "no browser error reported"}`);
  }

  const headings = [];
  for (const view of ["overview", "connections", "scenarios", "models"]) {
    await page.locator(`[data-view-target="${view}"]`).first().click();
    const heading = page.locator(`[data-view="${view}"] h1`);
    if (!(await heading.isVisible())) throw new Error(`${name}: ${view} did not become visible`);
    headings.push(await heading.textContent());
    if (view === "scenarios") {
      await page.locator("#simulate").click();
      await page.waitForFunction(() => !document.querySelector("#track-decision").disabled, null, { timeout: 10_000 });
      if (exerciseDecision && name === "desktop") {
        const journeyName = `Release routing proof ${Date.now()}`;
        await page.locator("#scenario-name").fill(journeyName);
        await page.locator("#track-decision").click();
        const decision = page.locator("[data-decision-row]").filter({ hasText: journeyName }).first();
        await decision.waitFor({ state: "visible" });

        const expectedErrorStart = errors.length;
        await decision.locator("[data-decision-status]").selectOption("validated");
        await decision.locator("[data-update-decision]").click();
        const decisionError = decision.locator("[data-decision-error]");
        const decisionErrorElement = await decisionError.elementHandle();
        await page.waitForFunction((element) => element.textContent.trim().length > 0, decisionErrorElement);
        const validationError = await decisionError.textContent();
        if (!validationError.toLowerCase().includes("passing quality")) {
          throw new Error(`${name}: quality gate did not reject an untested validation`);
        }
        errors.splice(expectedErrorStart);

        await decision.locator("[data-decision-status]").selectOption("rolled-out");
        await decision.locator("[data-decision-quality]").selectOption("pass");
        await decision.locator("[data-decision-measured]").fill("12.34");
        await decision.locator("[data-decision-notes]").fill("Release browser journey");
        await decision.locator("[data-update-decision]").click();
        await page.waitForFunction(
          () => document.querySelector(".decision-badge")?.textContent.trim() === "rolled out",
          null,
          { timeout: 10_000 },
        );
        const updatedDecision = page.locator("[data-decision-row]").filter({ hasText: journeyName }).first();
        await updatedDecision.screenshot({
          path: path.join(outputDirectory, `decision-proof-${name}.png`),
          timeout: 60_000,
        });
      }
      await page.screenshot({
        path: path.join(outputDirectory, `decision-lab-${name}.png`),
        fullPage: !exerciseDecision,
        timeout: 60_000,
      });
    }
  }
  await page.locator('[data-view-target="welcome"]').first().click();
  const welcomeHeading = page.locator('[data-view="welcome"] h1');
  if (!(await welcomeHeading.isVisible())) throw new Error(`${name}: welcome screen did not become visible`);
  headings.push(await welcomeHeading.textContent());
  await page.locator('[data-view-target="overview"]').first().click();
  await page.locator("#report-link").click();
  if (!(await page.locator("#report-dialog").isVisible())) throw new Error(`${name}: report dialog did not open`);
  await page.locator("#report-cancel").click();
  if (exerciseDecision && name === "desktop") {
    const realized = await page.locator("#metrics .metric").nth(2).locator("strong").textContent();
    const realizedValue = Number(realized.replace(/[^0-9.-]/g, ""));
    if (realizedValue < 12.34) throw new Error(`${name}: realized savings did not reach the overview`);
  }

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  if (dimensions.scrollWidth > dimensions.clientWidth + 1) {
    throw new Error(`${name}: horizontal overflow ${dimensions.scrollWidth}px > ${dimensions.clientWidth}px`);
  }
  if (errors.length) throw new Error(`${name}: ${errors.join("; ")}`);

  const screenshot = path.join(outputDirectory, `dashboard-${name}.png`);
  await page.screenshot({ path: screenshot, fullPage: !exerciseDecision, timeout: 60_000 });
  await page.close();
  return { name, headings, ...dimensions, screenshot };
}

async function main() {
  if (!executablePath) throw new Error("Chrome or Edge is required for the UI smoke test.");
  fs.mkdirSync(outputDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    const results = [];
    results.push(await inspectViewport(browser, "desktop", { width: 1440, height: 1000 }));
    results.push(await inspectViewport(browser, "mobile", { width: 390, height: 844 }));
    console.log(JSON.stringify({ url, executablePath, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
