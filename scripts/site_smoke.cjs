/* Browser-level smoke test for the static Poliora product site. */

const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const url = process.argv[2] || "http://127.0.0.1:8796";
const outputDirectory = path.resolve(process.argv[3] || "artifacts/site-smoke");
const chromeCandidates = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];
const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));

async function inspect(browser, name, viewport) {
  const page = await browser.newPage({ viewport });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin: url });
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  const response = await page.goto(url, { waitUntil: "networkidle", timeout: 20_000 });
  if (!response || !response.ok()) throw new Error(`${name}: site returned ${response?.status()}`);
  if ((await page.locator('link[rel="canonical"]').getAttribute("href")) !== "https://poliora.com/") {
    throw new Error(`${name}: canonical URL is missing or incorrect`);
  }
  const description = await page.locator('meta[name="description"]').getAttribute("content");
  if (!description || description.length < 80) throw new Error(`${name}: useful meta description missing`);
  const structuredData = await page.locator('script[type="application/ld+json"]').textContent();
  if (!structuredData || !JSON.parse(structuredData)["@graph"]) throw new Error(`${name}: structured data is invalid`);
  if (!(await page.locator(".brand-text").isVisible())) throw new Error(`${name}: product brand missing`);
  if (!(await page.locator("#calculator").isVisible())) throw new Error(`${name}: calculator missing`);
  if (!(await page.locator("#strategy").isVisible())) throw new Error(`${name}: decision path missing`);
  if (!(await page.locator("#download").isVisible())) throw new Error(`${name}: install section missing`);
  for (const href of [
    "downloads/Poliora-Setup-Windows.cmd",
    "downloads/Poliora-Setup-Mac.command",
  ]) {
    const installer = await page.request.get(`${url}/${href}`);
    if (!installer.ok()) throw new Error(`${name}: installer is unavailable: ${href}`);
  }

  await page.locator("#spendSlider").fill("500");
  await page.locator("#routeSlider").fill("80");
  const optimizedSpend = await page.locator("#optimizedSpend").textContent();
  if (optimizedSpend.trim() !== "$120.00") {
    throw new Error(`${name}: calculator result was ${optimizedSpend.trim()}, expected $120.00`);
  }
  await page.locator(".copy-btn").click();
  await page.waitForFunction(
    () => document.querySelector(".copy-btn")?.textContent.trim() === "Copied!",
    null,
    { timeout: 5_000 },
  );

  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  if (dimensions.scrollWidth > dimensions.clientWidth + 1) {
    throw new Error(`${name}: horizontal overflow ${dimensions.scrollWidth}px > ${dimensions.clientWidth}px`);
  }
  if (errors.length) throw new Error(`${name}: ${errors.join("; ")}`);

  const screenshot = path.join(outputDirectory, `site-${name}.png`);
  await page.screenshot({ path: screenshot, fullPage: true, timeout: 60_000 });
  await page.goto(`${url}/privacy.html`, { waitUntil: "networkidle", timeout: 20_000 });
  if (!(await page.getByRole("heading", { name: "Privacy boundary" }).isVisible())) {
    throw new Error(`${name}: privacy page missing`);
  }
  for (const pathName of ["robots.txt", "sitemap.xml", "llms.txt"]) {
    const asset = await page.request.get(`${url}/${pathName}`);
    if (!asset.ok()) throw new Error(`${name}: ${pathName} is unavailable`);
  }
  await page.close();
  return { name, ...dimensions, screenshot };
}

async function main() {
  if (!executablePath) throw new Error("Chrome or Edge is required for the site smoke test.");
  fs.mkdirSync(outputDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath });
  try {
    const results = [];
    results.push(await inspect(browser, "desktop", { width: 1440, height: 1000 }));
    results.push(await inspect(browser, "mobile", { width: 390, height: 844 }));
    console.log(JSON.stringify({ url, executablePath, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
