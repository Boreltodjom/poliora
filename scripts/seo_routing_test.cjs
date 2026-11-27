const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

async function main() {
  const source = fs.readFileSync(path.join(__dirname, '../site/_worker.js'), 'utf8');
  const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  let assertions = 0;
  for (const origin of ['https://poliora.pages.dev', 'https://www.poliora.com', 'http://www.poliora.com', 'http://poliora.com']) {
    for (const suffix of ['/', '/support?type=integration', '/privacy', '/assets/poliora-mark.png', '/sitemap.xml']) {
      let called = false;
      const response = await worker.fetch(new Request(`${origin}${suffix}`), { ASSETS: { fetch() { called = true; } } });
      assert.equal(response.status, 301);
      assert.equal(response.headers.get('location'), `https://poliora.com${suffix}`);
      assert.equal(called, false);
      assertions += 3;
    }
  }
  for (const origin of ['https://poliora.com', 'https://preview.poliora.pages.dev', 'http://localhost:8788']) {
    for (const method of ['GET', 'HEAD', 'POST']) {
      const request = new Request(`${origin}/support?type=bug`, { method });
      const assetResponse = new Response('asset', { status: 404 });
      const response = await worker.fetch(request, { ASSETS: { fetch(received) { assert.equal(received, request); return assetResponse; } } });
      assert.equal(response, assetResponse);
      assertions += 2;
    }
  }
  const sitemap = fs.readFileSync(path.join(__dirname, '../site/sitemap.xml'), 'utf8');
  assert(!sitemap.includes('.html'));
  const notFound = fs.readFileSync(path.join(__dirname, '../site/404.html'), 'utf8');
  assert(notFound.includes('noindex,follow'));
  console.log(`${assertions + 2} SEO routing assertions passed.`);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
