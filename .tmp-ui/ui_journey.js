const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const OUT = __dirname;
const BASE = 'http://localhost:5173';
const USER = process.env.UI_USER || '';
const PASS = process.env.UI_PASS || '';
if (!USER || !PASS) {
  console.error('Set UI_USER and UI_PASS env vars');
  process.exit(1);
}

const SUPPLIERS = [
  {
    name: 'Bid Food (Bidvest Food) UI',
    code: '366001UI',
    contact: 'Thomas',
    phone: '0370 3663 250',
    email: 'sloughsalescentre@bidfood.co.uk',
    lines: ['814 LEIGH ROAD', 'SLOUGH TRADING ESTATE', 'SLOUGH', 'BUCKS', 'SL1 4BD'],
  },
  {
    name: 'GEM SCIENTIFIC UI',
    code: '3MH001UI',
    contact: 'Steve/Katie',
    phone: '01509 613191',
    email: 'SALES@GEMSCIENTIFIC.CO.UK',
    lines: ['Unit 301 Baley Enterprise Centre', '513 Bradford Road', 'Batley', 'West Yorkshire', 'WF17 8LL'],
  },
];

async function shot(page, name) {
  const p = path.join(OUT, name);
  await page.screenshot({ path: p, fullPage: true });
  console.log('SHOT', name);
}

async function dump(page, label) {
  const t = (await page.locator('body').innerText()).slice(0, 800).replace(/\s+/g, ' ');
  console.log(label, page.url(), t);
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errs = [];
  page.on('response', async (r) => {
    const u = r.url();
    if (!/localhost:8000|cognito/.test(u)) return;
    if (r.status() >= 400) {
      let body = '';
      try { body = (await r.text()).slice(0, 250); } catch {}
      const row = { status: r.status(), method: r.request().method(), url: u, body };
      errs.push(row);
      console.log('HTTP_ERR', row.status, row.method, row.url, row.body);
    } else if (/\/container\//.test(u) && r.request().method() !== 'GET' && r.request().method() !== 'OPTIONS') {
      console.log('HTTP_OK', r.status(), r.request().method(), u);
    }
  });
  page.on('pageerror', (e) => console.log('PAGEERROR', e.message));

  // LOGIN
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  await page.getByLabel(/email|username/i).fill(USER);
  await page.getByLabel(/^password$/i).fill(PASS);
  await page.getByRole('button', { name: /sign in/i }).click();
  await page.waitForTimeout(4000);
  await shot(page, '10-after-login.png');
  await dump(page, 'AFTER_LOGIN');

  // Find managers / suppliers nav
  const navTries = [
    page.getByRole('link', { name: /manager/i }),
    page.getByRole('button', { name: /manager/i }),
    page.getByText(/container manager/i),
    page.getByText(/^suppliers$/i),
    page.locator('a[href*="manager"]'),
  ];
  for (const loc of navTries) {
    if (await loc.count() && await loc.first().isVisible()) {
      console.log('CLICK NAV', await loc.first().innerText().catch(() => '?'));
      await loc.first().click();
      await page.waitForTimeout(2000);
      break;
    }
  }
  // direct routes
  for (const route of ['/managers', '/managers/unified', '/container-manager', '/suppliers']) {
    await page.goto(BASE + route, { waitUntil: 'networkidle' }).catch(() => null);
    await page.waitForTimeout(1000);
    const body = await page.locator('body').innerText();
    if (/Create|Supplier|Container/i.test(body) && !/Sign in/i.test(body)) {
      console.log('REACHED', page.url());
      break;
    }
  }
  await shot(page, '11-managers.png');
  await dump(page, 'MANAGERS');

  // Prefer suppliers filter if present
  const supplierTab = page.getByRole('tab', { name: /supplier/i });
  if (await supplierTab.count()) {
    await supplierTab.first().click();
    await page.waitForTimeout(1000);
  }

  // SAD: open create empty
  const createBtn = page.getByRole('button', { name: /^create$/i }).or(page.getByRole('button', { name: /new/i })).or(page.getByRole('button', { name: /add/i }));
  if (await createBtn.count()) {
    await createBtn.first().click();
    await page.waitForTimeout(800);
    await shot(page, '12-create-open.png');
    // confirm empty
    const confirm = page.getByRole('button', { name: /^create$/i }).last();
    if (await confirm.count()) {
      await confirm.click();
      await page.waitForTimeout(800);
      console.log('SAD empty create text snippet:', (await page.locator('body').innerText()).match(/required|error|fail|missing/i)?.[0] || 'no obvious error');
      await shot(page, '13-sad-empty.png');
    }
  } else {
    console.log('NO CREATE BUTTON');
  }

  // HAPPY: create first supplier via dialog
  async function fillCreate(s) {
    // reopen if needed
    if (!(await page.locator('#um-create-name').count())) {
      const btn = page.getByRole('button', { name: /^create$/i }).or(page.getByRole('button', { name: /new|add/i }));
      if (await btn.count()) await btn.first().click();
      await page.waitForTimeout(500);
    }
    if (await page.locator('#um-create-name').count()) {
      await page.locator('#um-create-name').fill(s.name);
      await page.locator('#um-create-code').fill(s.code);
      const role = page.locator('#um-create-role');
      if (await role.count()) await role.selectOption({ label: 'Supplier' }).catch(async () => {
        await role.selectOption('supplier');
      });
      await page.getByRole('button', { name: /^create$/i }).last().click();
      await page.waitForTimeout(2500);
      await shot(page, `14-created-${s.code}.png`);
      await dump(page, 'CREATED_' + s.code);
      return true;
    }
    console.log('Create form not found');
    return false;
  }

  for (const s of SUPPLIERS) {
    const ok = await fillCreate(s);
    if (!ok) break;
    // open row by searching
    const search = page.getByPlaceholder(/search|filter/i).first();
    if (await search.count()) {
      await search.fill(s.code);
      await page.waitForTimeout(1000);
    }
    const row = page.getByText(s.code).first();
    if (await row.count()) {
      await row.click();
      await page.waitForTimeout(2000);
      await shot(page, `15-detail-${s.code}.png`);
      await dump(page, 'DETAIL_' + s.code);

      // Contacts tab
      const contactsTab = page.getByRole('tab', { name: /contact/i }).or(page.getByText(/^Contacts$/i));
      if (await contactsTab.count()) {
        await contactsTab.first().click();
        await page.waitForTimeout(800);
        // try fill contact fields
        const nameField = page.getByLabel(/^name$/i).or(page.locator('input[name="name"]')).first();
        const phoneField = page.getByLabel(/phone/i).first();
        const emailField = page.getByLabel(/email/i).first();
        if (await nameField.count()) await nameField.fill(s.contact);
        if (await phoneField.count()) await phoneField.fill(s.phone);
        if (await emailField.count()) await emailField.fill(s.email);
        const addBtn = page.getByRole('button', { name: /add contact|save|create|add$/i }).first();
        if (await addBtn.count()) {
          await addBtn.click();
          await page.waitForTimeout(1500);
        }
        await shot(page, `16-contact-${s.code}.png`);
      }

      // Addresses tab
      const addrTab = page.getByRole('tab', { name: /address/i }).or(page.getByText(/^Addresses$/i));
      if (await addrTab.count()) {
        await addrTab.first().click();
        await page.waitForTimeout(800);
        for (let i = 0; i < s.lines.length; i++) {
          const lab = page.getByLabel(new RegExp(`line\\s*${i + 1}|address.*${i + 1}`, 'i'));
          if (await lab.count()) await lab.first().fill(s.lines[i]);
        }
        const nameA = page.getByLabel(/^name$/i).first();
        if (await nameA.count()) await nameA.fill('Depot');
        const addA = page.getByRole('button', { name: /add address|save|create|add$/i }).first();
        if (await addA.count()) {
          await addA.click();
          await page.waitForTimeout(1500);
        }
        await shot(page, `17-address-${s.code}.png`);
      }
    } else {
      console.log('ROW NOT FOUND', s.code);
    }

    // back to list
    await page.goto(page.url().replace(/\/\d+.*/, '').replace(/\/$/, '') || BASE + '/managers', { waitUntil: 'networkidle' }).catch(() => null);
    await page.waitForTimeout(1000);
  }

  // SAD duplicate code
  if (await createBtn.count() || await page.getByRole('button', { name: /^create$/i }).count()) {
    const btn = page.getByRole('button', { name: /^create$/i }).first();
    await btn.click().catch(() => null);
    await page.waitForTimeout(500);
    if (await page.locator('#um-create-name').count()) {
      await page.locator('#um-create-name').fill('Dup Bid');
      await page.locator('#um-create-code').fill('366001UI');
      await page.getByRole('button', { name: /^create$/i }).last().click();
      await page.waitForTimeout(2000);
      await shot(page, '18-sad-dup.png');
      await dump(page, 'SAD_DUP');
    }
  }

  fs.writeFileSync(path.join(OUT, 'ui-errs.json'), JSON.stringify(errs, null, 2));
  console.log('DONE errs=', errs.length);
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
