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

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const net = [];

page.on('response', async (res) => {
  const url = res.url();
  if (
    url.includes('/stock/') ||
    url.includes('login') ||
    url.includes('auth') ||
    url.includes('/api')
  ) {
    let body = '';
    try {
      body = (await res.text()).slice(0, 800);
    } catch {}
    let reqBody = '';
    try {
      reqBody = res.request().postData()?.slice(0, 800) || '';
    } catch {}
    net.push({
      status: res.status(),
      method: res.request().method(),
      url,
      reqBody,
      body,
    });
  }
});
page.on('console', (msg) => {
  if (msg.type() === 'error') log('CONSOLE_ERR: ' + msg.text());
});

try {
  await page.goto('http://localhost:5173/production/warehouse', {
    waitUntil: 'domcontentloaded',
    timeout: 45000,
  });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, '01-initial.png'), fullPage: true });
  log('URL after goto: ' + page.url());

  const passSel = page.locator('input[type="password"]').first();
  if ((await passSel.count()) && (await passSel.isVisible())) {
    log('Login form detected');
    const inputs = page.locator('input:visible');
    const n = await inputs.count();
    for (let i = 0; i < n; i++) {
      log(
        `input ${i}: type=${await inputs.nth(i).getAttribute('type')} name=${await inputs.nth(i).getAttribute('name')} ph=${await inputs.nth(i).getAttribute('placeholder')} id=${await inputs.nth(i).getAttribute('id')}`,
      );
    }
    const textInputs = page.locator(
      'input:visible:not([type="password"]):not([type="hidden"]):not([type="checkbox"]):not([type="submit"])',
    );
    if (await textInputs.count()) await textInputs.first().fill('utsavgohel');
    await passSel.fill('Gazebo@2025');
    await page.screenshot({ path: path.join(OUT, '02-login-filled.png'), fullPage: true });
    const loginBtn = page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).first();
    if (await loginBtn.count()) await loginBtn.click();
    else await page.keyboard.press('Enter');
    await page.waitForTimeout(3000);
    await page.goto('http://localhost:5173/production/warehouse', {
      waitUntil: 'domcontentloaded',
      timeout: 45000,
    });
    await page.waitForTimeout(2000);
  }

  await page.screenshot({ path: path.join(OUT, '03-warehouse.png'), fullPage: true });
  log('URL warehouse: ' + page.url());
  const text = await page.locator('body').innerText();
  fs.writeFileSync(path.join(OUT, '03-body.txt'), text);
  log('BODY_SNIP: ' + text.slice(0, 2000).replace(/\n/g, ' | '));

  const clickables = await page
    .locator('button, a, [role="tab"], [role="button"]')
    .allTextContents();
  log(
    'CLICKABLES: ' +
      JSON.stringify(clickables.map((t) => t.trim()).filter(Boolean).slice(0, 100)),
  );

  // Prefer Goods Out tab/button
  const candidates = [
    page.getByRole('button', { name: /goods\s*out/i }),
    page.getByRole('tab', { name: /goods\s*out/i }),
    page.getByText(/goods\s*out/i),
  ];
  for (const c of candidates) {
    if ((await c.count()) > 0) {
      log('Click Goods Out: ' + (await c.first().innerText()).slice(0, 80));
      await c.first().click();
      await page.waitForTimeout(1200);
      break;
    }
  }

  await page.screenshot({ path: path.join(OUT, '04-goods-out.png'), fullPage: true });
  const text2 = await page.locator('body').innerText();
  fs.writeFileSync(path.join(OUT, '04-body.txt'), text2);
  log('GO_BODY: ' + text2.slice(0, 2500).replace(/\n/g, ' | '));

  const scan = page
    .locator(
      'input[placeholder*="GS1" i], input[placeholder*="serial" i], input[placeholder*="scan" i], input[placeholder*="barcode" i], input[placeholder*="bag" i]',
    )
    .first();

  if (await scan.count()) {
    log('Scan input ph=' + (await scan.getAttribute('placeholder')));
    await scan.click();
    await scan.fill('(10)26217(17)260813(21)S3QE6R30ZTZ3');
    const addBtn = page.getByRole('button', { name: /^add$/i }).first();
    if (await addBtn.count()) await addBtn.click();
    else await scan.press('Enter');
    await page.waitForTimeout(2000);
    await page.screenshot({ path: path.join(OUT, '05-after-scan.png'), fullPage: true });
    const text3 = await page.locator('body').innerText();
    fs.writeFileSync(path.join(OUT, '05-body.txt'), text3);
    log('AFTER_SCAN: ' + text3.slice(0, 2500).replace(/\n/g, ' | '));

    // second scan same bag - should block if FE/BE correct
    await scan.fill('(10)26217(17)260813(21)S3QE6R30ZTZ3');
    if (await addBtn.count()) await addBtn.click();
    else await scan.press('Enter');
    await page.waitForTimeout(1200);
    await page.screenshot({ path: path.join(OUT, '06-duplicate-scan.png'), fullPage: true });
    log('DUP_BODY: ' + (await page.locator('body').innerText()).slice(0, 1500).replace(/\n/g, ' | '));
  } else {
    log('NO_SCAN_INPUT');
    const all = page.locator('input');
    const c = await all.count();
    for (let i = 0; i < c; i++) {
      log(
        `all_input ${i} type=${await all.nth(i).getAttribute('type')} ph=${await all.nth(i).getAttribute('placeholder')} name=${await all.nth(i).getAttribute('name')}`,
      );
    }
  }

  // destination
  const selects = page.locator('select');
  const sc = await selects.count();
  log('select count=' + sc);
  for (let i = 0; i < sc; i++) {
    const opts = await selects.nth(i).locator('option').allTextContents();
    log('SELECT[' + i + ']: ' + JSON.stringify(opts));
  }

  // Look for department dropdown custom
  const dept = page.getByText(/to department|spice room|destination/i);
  log('dept mentions: ' + (await dept.count()));

  const confirm = page.getByRole('button', { name: /confirm|transfer|submit|post/i });
  log('CONFIRM_BTNS: ' + JSON.stringify(await confirm.allTextContents()));
  for (let i = 0; i < (await confirm.count()); i++) {
    const disabled = await confirm.nth(i).isDisabled();
    log(`confirm[${i}] disabled=${disabled} text=${await confirm.nth(i).innerText()}`);
  }

  // Feature checklist from page text
  const blob = (await page.locator('body').innerText()).toLowerCase();
  const checks = {
    multiScanCart: /cart/.test(blob),
    toDepartment: /to department|destination/.test(blob),
    manualFifo: /manual|fifo/.test(blob),
    locationBalanceHint: /left|balance|warehouse/.test(blob),
    unitMovesHint: /unit_moves|serial/.test(blob),
    partialQty: /\/\s*\d+|qty/.test(blob),
  };
  log('FEATURE_FLAGS: ' + JSON.stringify(checks));

  fs.writeFileSync(path.join(OUT, 'network.json'), JSON.stringify(net, null, 2));
  fs.writeFileSync(path.join(OUT, 'findings.txt'), findings.join('\n'));
  log('DONE net_calls=' + net.length);
} catch (e) {
  log('FATAL: ' + (e.stack || e));
  try {
    await page.screenshot({ path: path.join(OUT, 'error.png'), fullPage: true });
  } catch {}
} finally {
  await browser.close();
}
