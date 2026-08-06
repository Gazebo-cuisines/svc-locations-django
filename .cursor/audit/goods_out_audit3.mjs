import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const OUT =
  'C:/Users/varun/projects/gazeboo-cloud/microservice/svc-locations-django/.cursor/audit';
const BARCODE = '(10)26217(17)260813(21)S3QE6R30ZTZ3';
const log = (...a) => console.log(...a);

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const transfers = [];

page.on('request', (req) => {
  if (req.url().includes('/stock/transfer/') && req.method() === 'POST') {
    transfers.push({ phase: 'req', body: req.postData() });
    log('REQ', req.postData());
  }
});
page.on('response', async (res) => {
  if (res.url().includes('/stock/transfer/') && res.request().method() === 'POST') {
    const body = await res.text().catch(() => '');
    transfers.push({ phase: 'res', status: res.status(), body: body.slice(0, 2000) });
    log('RES', res.status(), body.slice(0, 500));
  }
});

try {
  await page.goto('http://localhost:5173/production/warehouse', {
    waitUntil: 'domcontentloaded',
    timeout: 45000,
  });
  await page.waitForTimeout(1200);

  if (await page.locator('input[type="password"]').isVisible().catch(() => false)) {
    await page.locator('#email').fill('utsavgohel');
    await page.locator('input[type="password"]').fill('Gazebo@2025');
    const btn = page.locator('button[type="submit"], button').filter({ hasText: /log|sign/i }).first();
    await btn.click();
    await page.waitForTimeout(3000);
    await page.goto('http://localhost:5173/production/warehouse', {
      waitUntil: 'domcontentloaded',
    });
    await page.waitForTimeout(1200);
  }

  await page.getByText(/Unit 2 — Raw Material/i).first().click();
  await page.waitForTimeout(1500);
  await page.locator('.warehouse__nav, nav, aside, .warehouse').getByText(/Goods Out/i).first().click().catch(async () => {
    await page.getByText(/Send stock to production/i).first().click();
  });
  await page.waitForTimeout(1000);

  // Open department ImageSelect: click the control under To department
  const field = page.locator('.form-field').filter({ hasText: 'To department' });
  await field.locator('button, [role="combobox"], .image-select__control, input').first().click();
  await page.waitForTimeout(300);
  // Prefer typing in any open search
  const openInput = page.locator('input:visible').filter({ has: page.locator(':scope') });
  // fill search departments placeholder if present
  const deptSearch = page.locator('input[placeholder*="departments" i]:visible');
  if (await deptSearch.count()) {
    await deptSearch.first().fill('Spice');
    await page.waitForTimeout(400);
  }
  await page.getByRole('option', { name: /Spice Room/i }).first().click().catch(async () => {
    await page.locator('[role="option"], li, button, div').filter({ hasText: /^Spice Room$/i }).first().click();
  });
  await page.waitForTimeout(400);
  log('dept selected, body snippet:', (await page.locator('body').innerText()).match(/To department[\s\S]{0,80}/)?.[0]);

  const scan = page.locator('input[placeholder*="GS1" i]');
  await scan.fill(BARCODE);
  await page.getByRole('button', { name: /^Add$/ }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, '20-ready.png'), fullPage: true });
  log('cart:', (await page.locator('body').innerText()).match(/Cart ·[^\n]+/)?.[0]);

  const confirm = page.getByRole('button', { name: /Confirm Goods Out/i });
  log('disabled', await confirm.isDisabled());
  if (!(await confirm.isDisabled())) {
    await confirm.click();
    await page.waitForTimeout(2500);
  }
  await page.screenshot({ path: path.join(OUT, '21-posted.png'), fullPage: true });
  log('after confirm:', (await page.locator('body').innerText()).slice(0, 1200).replace(/\n/g, ' | '));

  await scan.fill(BARCODE);
  await page.getByRole('button', { name: /^Add$/ }).click();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, '22-rescan.png'), fullPage: true });
  log('rescan:', (await page.locator('body').innerText()).match(/Duplicate|Not at|Empty|managers-banner|SUGAR|Cart ·[^\n]+|alert[\s\S]{0,120}/)?.[0]);
  log('rescan body:', (await page.locator('body').innerText()).slice(0, 1500).replace(/\n/g, ' | '));

  fs.writeFileSync(path.join(OUT, 'transfers.json'), JSON.stringify(transfers, null, 2));
} catch (e) {
  log('FATAL', e);
  await page.screenshot({ path: path.join(OUT, 'error3.png'), fullPage: true }).catch(() => {});
} finally {
  await browser.close();
}
