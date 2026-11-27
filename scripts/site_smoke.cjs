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
const executablePath = process.env.POLIORA_BROWSER_EXECUTABLE || chromeCandidates.find((candidate) => fs.existsSync(candidate));

async function inspect(browser, name, viewport) {
  const page = await browser.newPage({ viewport, reducedMotion: "reduce" });
  await page.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      const style = document.createElement("style");
      style.textContent = "*,*::before,*::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }";
      document.head.appendChild(style);
    });
  });
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
  if (!(await page.locator("#detection").isVisible())) throw new Error(`${name}: local history section missing`);
  if (!(await page.locator("#calculator").isVisible())) throw new Error(`${name}: calculator missing`);
  if (!(await page.locator("#strategy").isVisible())) throw new Error(`${name}: decision path missing`);
  if (!(await page.locator("#download").isVisible())) throw new Error(`${name}: install section missing`);
  if ((await page.locator('h1').textContent()).trim() !== 'Poliora') throw new Error(`${name}: product heading missing`);
  if ((await page.locator('.faq-list details').count()) < 10) throw new Error(`${name}: expanded FAQ missing`);
  const brokenImages = await page.locator('img').evaluateAll((images) => images.filter((image) => !image.complete || !image.naturalWidth).map((image) => image.src));
  if (brokenImages.length) throw new Error(`${name}: broken images: ${brokenImages.join(', ')}`);
  const missingAnchors = await page.locator('a[href^="#"]').evaluateAll((links) => links.filter((link) => !document.getElementById(link.hash.slice(1))).map((link) => link.hash));
  if (missingAnchors.length) throw new Error(`${name}: missing anchor targets: ${missingAnchors.join(', ')}`);
  const windowsDownload = await page.getByRole('link', { name: 'Download for Windows', exact: true }).getAttribute('href');
  const macDownload = await page.getByRole('link', { name: 'Download for macOS (Apple Silicon)', exact: true }).getAttribute('href');
  if (windowsDownload !== "https://github.com/Boreltodjom/poliora/releases/latest/download/Poliora-Setup-Windows.exe") {
    throw new Error(`${name}: Windows desktop installer link is incorrect`);
  }
  if (macDownload !== "https://github.com/Boreltodjom/poliora/releases/latest/download/Poliora-macOS-AppleSilicon.zip") {
    throw new Error(`${name}: macOS desktop download link is incorrect`);
  }
  if ((await page.locator('.cli-cmd').textContent()).trim() !== 'pip install poliora && poliora detect') {
    throw new Error(`${name}: first-run detection command is missing`);
  }

  await page.locator("#spendSlider").fill("500");
  await page.locator("#routeSlider").fill("80");
  const optimizedSpend = await page.locator("#optimizedSpend").textContent();
  if (optimizedSpend.trim() !== "$120.00") {
    throw new Error(`${name}: calculator result was ${optimizedSpend.trim()}, expected $120.00`);
  }
  for (const spend of [20, 260, 500, 2000]) {
    for (const percentage of [0, 1, 50, 80, 90, 99, 100]) {
      await page.locator('#spendSlider').fill(String(spend));
      await page.locator('#routeSlider').fill(String(percentage));
      const actual = (await page.locator('#optimizedSpend').textContent()).trim();
      const expected = `$${(spend * (1 - percentage / 100 * 0.95)).toFixed(2)}`;
      if (actual !== expected) throw new Error(`${name}: calculator ${spend}/${percentage} gave ${actual}, expected ${expected}`);
    }
  }
  for (const summary of await page.locator('.faq-list summary').all()) {
    await summary.click();
    if (!(await summary.locator('..').getAttribute('open') === '')) throw new Error(`${name}: FAQ failed to open`);
    await summary.click();
  }
  await page.locator('.developer-install summary').click();
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
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: path.join(outputDirectory, `site-${name}-first.png`), timeout: 60_000 });
  await page.screenshot({ path: screenshot, fullPage: true, timeout: 60_000 });
  await page.goto(`${url}/privacy.html`, { waitUntil: "networkidle", timeout: 20_000 });
  if (!(await page.getByRole("heading", { name: "Your workspace is not our analytics." }).isVisible())) {
    throw new Error(`${name}: privacy page missing`);
  }
  if (await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)) throw new Error(`${name}: privacy page overflow`);
  await page.goto(`${url}/support.html?type=integration`, { waitUntil: 'networkidle' });
  if (await page.locator('#feedback-type').inputValue() !== 'integration') throw new Error(`${name}: support category link failed`);
  if (!(await page.locator('#draft-preview').isHidden())) throw new Error(`${name}: draft visible before input`);
  await page.getByRole('button', { name: 'Preview feedback' }).click();
  if (!(await page.locator('#draft-preview').isHidden())) throw new Error(`${name}: blank form accepted`);
  const transmissions = [];
  page.on('request', (request) => {
    if (request.method() !== 'GET' || !request.url().startsWith(url)) transmissions.push(request.url());
  });
  const hostileText = '<img src=x onerror=alert(1)> & quota issue';
  await page.locator('#feedback-subject').fill('Missing integration & quota');
  await page.locator('#feedback-message').fill(hostileText);
  await page.locator('#feedback-os').selectOption('Windows');
  await page.locator('#feedback-version').fill('0.5.1');
  await page.getByRole('button', { name: 'Preview feedback' }).click();
  if (!(await page.locator('#draft-preview').isVisible())) throw new Error(`${name}: draft not generated`);
  const draft = await page.locator('#draft-text').textContent();
  if (!draft.includes('support@poliora.com') || !draft.includes(hostileText) || !draft.includes('0.5.1')) throw new Error(`${name}: draft lost user input`);
  if (await page.locator('#draft-text img').count()) throw new Error(`${name}: feedback HTML was executed`);
  const mailto = new URL(await page.locator('#email-draft').getAttribute('href'));
  if (mailto.protocol !== 'mailto:' || mailto.pathname !== 'support@poliora.com' || !mailto.searchParams.get('body').includes(hostileText)) throw new Error(`${name}: email draft encoding incorrect`);
  const github = new URL(await page.locator('#github-draft').getAttribute('href'));
  if (github.origin !== 'https://github.com' || github.pathname !== '/Boreltodjom/poliora/issues/new' || !github.searchParams.get('body').includes(hostileText)) throw new Error(`${name}: GitHub draft encoding incorrect`);
  if (!(await page.locator('#email-draft').isHidden())) throw new Error(`${name}: unconfigured support inbox offered as active`);
  await page.getByRole('button', { name: 'Copy draft' }).click();
  await page.waitForFunction(() => document.querySelector('#draft-status').textContent.startsWith('Copied.'));
  if (transmissions.length) throw new Error(`${name}: form unexpectedly transmitted data: ${transmissions.join(', ')}`);
  const stored = await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }));
  if (stored.local || stored.session) throw new Error(`${name}: feedback persisted browser data`);
  if (await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1)) throw new Error(`${name}: support page overflow`);
  await page.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await page.screenshot({ path: path.join(outputDirectory, `support-${name}.png`), fullPage: true, timeout: 60_000 });
  await page.locator('#feedback-subject').fill('Changed after preview');
  if (!(await page.locator('#draft-preview').isHidden())) throw new Error(`${name}: stale draft still offered after editing`);
  if (errors.length) throw new Error(`${name}: ${errors.join('; ')}`);
  for (const pathName of ["robots.txt", "sitemap.xml", "llms.txt"]) {
    const asset = await page.request.get(`${url}/${pathName}`);
    if (!asset.ok()) throw new Error(`${name}: ${pathName} is unavailable`);
  }
  if (url === 'https://poliora.com') {
    const sitemapResponse = await page.request.get(`${url}/sitemap.xml`);
    const entries = await page.evaluate((xml) => {
      const document = new DOMParser().parseFromString(xml, 'application/xml');
      if (document.querySelector('parsererror')) throw new Error('Sitemap XML is invalid');
      return [...document.getElementsByTagName('loc')].map((entry) => entry.textContent.trim());
    }, await sitemapResponse.text());
    for (const entry of entries) {
      const target = await page.request.get(entry, { maxRedirects: 0 });
      if (target.status() !== 200) throw new Error(`${name}: sitemap entry is not a direct 200: ${entry}`);
      const canonical = await page.evaluate((html) => new DOMParser().parseFromString(html, 'text/html').querySelector('link[rel="canonical"]')?.href, await target.text());
      if (canonical !== entry) throw new Error(`${name}: sitemap/canonical mismatch: ${entry} -> ${canonical}`);
    }
    const missing = await page.request.get(`${url}/seo-check-page-that-does-not-exist`);
    if (missing.status() !== 404) throw new Error(`${name}: missing page is not a real 404`);
    const alias = await page.request.get('https://poliora.pages.dev/support?type=integration', { maxRedirects: 0 });
    if (alias.status() !== 301 || alias.headers().location !== `${url}/support?type=integration`) throw new Error(`${name}: temporary domain is not permanently redirected`);
  }
  await page.close();
  return { name, ...dimensions, screenshot };
}

async function main() {
  if (!executablePath) throw new Error("Chrome or Edge is required for the site smoke test.");
  fs.mkdirSync(outputDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath, args: ["--disable-gpu"] });
  try {
    const results = [];
    results.push(await inspect(browser, "desktop", { width: 1440, height: 1000 }));
    results.push(await inspect(browser, "mobile", { width: 390, height: 844 }));
    results.push(await inspect(browser, "small-mobile", { width: 320, height: 740 }));
    results.push(await inspect(browser, "tablet", { width: 768, height: 1024 }));
    results.push(await inspect(browser, "laptop", { width: 1024, height: 768 }));
    results.push(await inspect(browser, "wide", { width: 1920, height: 1080 }));
    console.log(JSON.stringify({ url, executablePath, results }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
