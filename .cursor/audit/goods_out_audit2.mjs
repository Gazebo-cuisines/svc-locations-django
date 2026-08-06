import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const OUT =
  'C:/Users/varun/projects/gazeboo-cloud/microservice/svc-locations-django/.cursor/audit';
fs.mkdirSync(OUT, { recursive: true });
const findings = [];
const log = (m) => {
  console.log(m);
  findings.push(String(m));
};

const BARCODE = '(10)26217(17)260813(21)S3QE6R30ZTZ3';
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const net = [];

page.on('response', async (res) => {
  const url = res.url();
  if (!url.includes('localhost:8000') && !url.includes('/stock/')) return;
  let body = '';
  let reqBody = '';
  try {
    body = (await res.text()).slice(0, 1200);
  } catch {}
  try {
    reqBody = res.request().postData()?.slice(0, 1200) || '';
  } catch {}
  net.push({
    status: res.status(),
    method: res.request().method(),
    url,
    reqBody,
    body,
  });
  log(`NET ${res.request().method()} ${res.status()} ${url}`);
});

async function login() {
  await page.goto('http://localhost:5173/login', { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForTimeout(800);
  if (await page.locator('input[type="password"]').count()) {
    await page.locator('#email, input[name="email"]').first().fill('utsavgohel');
    await page.locator('input[type="password"]').fill('Gazebo@2025');
    await page.getByRole('button', { name: /log\s*in|sign\s*in/i }).first().click();
    await page.waitForTimeout(3000);
  }
}

try {
  await login();
  await page.goto('http://localhost:5173/production/warehouse', {
    waitUntil: 'domcontentloaded',
    timeout: 45000,
  });
  await page.waitForTimeout(1500);

  // Select Unit 2
  await page.getByText(/Unit 2/i).first().click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, '10-unit2.png'), fullPage: true });
  log('URL after Unit2: ' + page.url());
  fs.writeFileSync(path.join(OUT, '10-body.txt'), await page.locator('body').innerText());
  log('BODY: ' + (await page.locator('body').innerText()).slice(0, 2500).replace(/\n/g, ' | '));

  const tabs = await page.locator('button, a, [role="tab"]').allTextContents();
  log('TABS: ' + JSON.stringify(tabs.map((t) => t.trim()).filter(Boolean).slice(0, 120)));

  // Click Goods Out if present
  const go = page.getByRole('button', { name: /goods\s*out/i }).or(page.getByText(/^Goods Out$/i));
  if ((await go.count()) > 0) {
    await go.first().click();
    await page.waitForTimeout(1500);
  } else {
    // try nav links
    const link = page.locator('a, button').filter({ hasText: /goods\s*out/i });
    if (await link.count()) {
      await link.first().click();
      await page.waitForTimeout(1500);
    } else log('NO Goods Out control found on Unit2 home');
  }

  await page.screenshot({ path: path.join(OUT, '11-goods-out.png'), fullPage: true });
  fs.writeFileSync(path.join(OUT, '11-body.txt'), await page.locator('body').innerText());
  log('GO: ' + (await page.locator('body').innerText()).slice(0, 3000).replace(/\n/g, ' | '));

  // If still not there, try common routes
  const routes = [
    '/production/warehouse/unit-2/goods-out',
    '/production/warehouse/8/goods-out',
    '/production/warehouse/goods-out',
    '/stock/goods-out',
    '/production/stock/goods-out',
  ];
  let onGo = /goods\s*out|scan bags|to department/i.test(await page.locator('body').innerText());
  if (!onGo) {
    for (const r of routes) {
      await page.goto('http://localhost:5173' + r, { waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => null);
      await page.waitForTimeout(1000);
      const t = await page.locator('body').innerText();
      log('TRY ' + r + ' => ' + page.url() + ' :: ' + t.slice(0, 200).replace(/\n/g, ' | '));
      if (/goods\s*out|scan bags|to department/i.test(t)) {
        onGo = true;
        break;
      }
    }
  }

  await page.screenshot({ path: path.join(OUT, '12-goods-out-final.png'), fullPage: true });
  const body = await page.locator('body').innerText();
  fs.writeFileSync(path.join(OUT, '12-body.txt'), body);
  log('FINAL_URL: ' + page.url());
  log('FINAL_BODY: ' + body.slice(0, 3500).replace(/\n/g, ' | '));

  const scan = page
    .locator(
      'input[placeholder*="GS1" i], input[placeholder*="serial" i], input[placeholder*="scan" i], input[placeholder*="barcode" i]',
    )
    .first();

  if (await scan.count()) {
    // Scan once
    await scan.fill(BARCODE);
    const addBtn = page.getByRole('button', { name: /^add$/i }).first();
    if (await addBtn.count()) await addBtn.click();
    else await scan.press('Enter');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: path.join(OUT, '13-scan1.png'), fullPage: true });
    log('SCAN1: ' + (await page.locator('body').innerText()).slice(0, 2000).replace(/\n/g, ' | '));

    // Duplicate scan
    await scan.fill(BARCODE);
    if (await addBtn.count()) await addBtn.click();
    else await scan.press('Enter');
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(OUT, '14-scan-dup.png'), fullPage: true });
    log('SCANDUP: ' + (await page.locator('body').innerText()).slice(0, 1500).replace(/\n/g, ' | '));

    // Pick Spice Room
    const select = page.locator('select').first();
    if (await select.count()) {
      const opts = await select.locator('option').allTextContents();
      log('OPTS: ' + JSON.stringify(opts));
      const idx = opts.findIndex((o) => /spice/i.test(o));
      if (idx >= 0) await select.selectOption({ index: idx });
    } else {
      // custom dropdown
      const to = page.getByText(/to department/i);
      if (await to.count()) {
        // click nearby combobox
        const combo = page.locator('[role="combobox"], button').filter({ hasText: /select|spice|department|choose/i });
        log('combo count ' + (await combo.count()));
      }
      // try click any spice room option visible after opening select-like
      const anySelectish = page.locator('select, [role="listbox"], [aria-haspopup="listbox"]');
      log('selectish ' + (await anySelectish.count()));
    }

    // Look for native select labeled To
    const allSelects = page.locator('select');
    for (let i = 0; i < (await allSelects.count()); i++) {
      const opts = await allSelects.nth(i).locator('option').allTextContents();
      log(`select${i}: ${JSON.stringify(opts)}`);
      const spice = opts.findIndex((o) => /spice/i.test(o));
      if (spice >= 0) await allSelects.nth(i).selectOption({ index: spice });
    }

    // Also try clicking a dropdown that shows placeholder
    const dd = page.locator('button, div[role="button"]').filter({ hasText: /select department|choose|spice room|—/i });
    if (await dd.count()) {
      await dd.first().click();
      await page.waitForTimeout(500);
      const spiceOpt = page.getByText(/spice room/i);
      if (await spiceOpt.count()) await spiceOpt.first().click();
    }

    await page.waitForTimeout(500);
    const confirm = page.getByRole('button', { name: /confirm/i }).first();
    if (await confirm.count()) {
      log('confirm disabled=' + (await confirm.isDisabled()));
      if (!(await confirm.isDisabled())) {
        await confirm.click();
        await page.waitForTimeout(2500);
        await page.screenshot({ path: path.join(OUT, '15-after-confirm.png'), fullPage: true });
        log('AFTER_CONFIRM: ' + (await page.locator('body').innerText()).slice(0, 2000).replace(/\n/g, ' | '));
      }
    }
  } else {
    log('NO SCAN FIELD on final page');
    // dump links for navigation map
    const hrefs = await page.locator('a[href]').evaluateAll((as) =>
      as.map((a) => ({ href: a.getAttribute('href'), text: (a.textContent || '').trim() })).slice(0, 80),
    );
    log('HREFS: ' + JSON.stringify(hrefs));
  }

  fs.writeFileSync(path.join(OUT, 'network2.json'), JSON.stringify(net, null, 2));
  fs.writeFileSync(path.join(OUT, 'findings2.txt'), findings.join('\n'));
} catch (e) {
  log('FATAL: ' + (e.stack || e));
  await page.screenshot({ path: path.join(OUT, 'error2.png'), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
