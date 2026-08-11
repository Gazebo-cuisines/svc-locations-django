const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const OUT = path.resolve(__dirname);
const BASE = 'http://localhost:5173';

async function shot(page, name) {
  const p = path.join(OUT, name);
  await page.screenshot({ path: p, fullPage: true });
  console.log('SHOT', p);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const net = [];
  page.on('response', async (r) => {
    const u = r.url();
    if (!/localhost:8000|localhost:5173|cognito|gazeboo/.test(u)) return;
    if (r.request().resourceType() === 'stylesheet' || r.request().resourceType() === 'image' || r.request().resourceType() === 'font') return;
    const entry = { status: r.status(), method: r.request().method(), url: u };
    if (r.status() >= 400) {
      try { entry.body = (await r.text()).slice(0, 300); } catch {}
      console.log('HTTP_ERR', entry.status, entry.method, entry.url, entry.body || '');
    }
    net.push(entry);
  });
  page.on('pageerror', (e) => console.log('PAGEERROR', e.message));

  await page.goto(BASE + '/', { waitUntil: 'networkidle', timeout: 45000 });
  console.log('URL', page.url());
  await shot(page, '01-landing.png');
  console.log('BODY', (await page.locator('body').innerText()).slice(0, 1200));

  // Try common nav to managers/suppliers
  const candidates = [
    'text=Managers',
    'text=Suppliers',
    'text=Containers',
    'text=Locations',
    'a[href*="supplier"]',
    'a[href*="manager"]',
    'button:has-text("Sign in")',
    'button:has-text("Login")',
    'text=Sign in',
  ];
  for (const sel of candidates) {
    const loc = page.locator(sel).first();
    if (await loc.count()) {
      console.log('FOUND', sel, 'visible=', await loc.isVisible().catch(() => false));
    }
  }

  // Try navigate known routes
  for (const route of ['/managers', '/suppliers', '/containers', '/managers/suppliers', '/app/managers', '/login']) {
    try {
      const resp = await page.goto(BASE + route, { waitUntil: 'networkidle', timeout: 15000 });
      console.log('ROUTE', route, '->', page.url(), 'status', resp && resp.status());
      const t = (await page.locator('body').innerText()).slice(0, 400).replace(/\s+/g, ' ');
      console.log('  text', t);
      await shot(page, `route-${route.replace(/\W+/g, '_')}.png`);
    } catch (e) {
      console.log('ROUTE_FAIL', route, e.message);
    }
  }

  fs.writeFileSync(path.join(OUT, 'net.json'), JSON.stringify(net, null, 2));
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
