'use strict';

const { chromium } = require('playwright');
const fs   = require('fs');
const path = require('path');

const CHROMIUM_REPLIT = '/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium';
const CHROMIUM_CACHE  = '/home/runner/workspace/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell';
const CHROMIUM        = fs.existsSync(CHROMIUM_REPLIT) ? CHROMIUM_REPLIT
                      : fs.existsSync(CHROMIUM_CACHE)  ? CHROMIUM_CACHE
                      : undefined;

const DIR          = __dirname;
const COOKIES_FILE = path.join(DIR, 'aviso_cookies.json');
const STATE_FILE   = path.join(DIR, 'aviso_2fa_state.json');

const OTP = process.argv[2];
if (!OTP) {
  console.error('[phase2] Usage: node aviso_login_phase2.cjs <OTP>');
  process.exit(1);
}
if (!fs.existsSync(STATE_FILE)) {
  console.error('[phase2] State file nahi mili — pehle phase1 chalao');
  process.exit(1);
}

const { twoFaUrl, cookies: savedCookies } = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));

(async () => {
  console.log('[phase2] Browser launch…');
  const browser = await chromium.launch({
    executablePath: CHROMIUM,
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
    headless: true,
  });

  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  });

  // Restore cookies from phase 1
  await context.addCookies(savedCookies);
  const page = await context.newPage();

  try {
    console.log('[phase2] 2FA page pe ja raha hoon…');
    await page.goto(twoFaUrl, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(1500);

    console.log('[phase2] OTP fill kar raha hoon:', OTP);
    const inputs = await page.locator('input:not([type="hidden"])').all();
    if (inputs.length > 0) {
      await inputs[0].click();
      await page.waitForTimeout(300);
      await inputs[0].fill(OTP);
    } else {
      console.warn('[phase2] OTP input field nahi mila, keyboard se type karta hoon');
      await page.keyboard.type(OTP, { delay: 100 });
    }

    await page.waitForTimeout(800);
    console.log('[phase2] Submit kar raha hoon…');
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {}),
      page.locator('button[type="submit"], button').first().click().catch(() => page.keyboard.press('Enter')),
    ]);

    const url = page.url();
    console.log('[phase2] URL after OTP:', url);

    const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 600));
    const ok = /Выход|Logout|Баланс|Кабинет/i.test(bodyText);
    if (!ok) {
      console.error('[phase2] Login verify FAIL. Page preview:', bodyText.substring(0, 300));
      throw new Error('OTP galat ho sakta hai ya session expire ho gayi');
    }

    const cookies = await context.cookies();
    fs.writeFileSync(COOKIES_FILE, JSON.stringify(cookies, null, 2));
    fs.unlinkSync(STATE_FILE);
    console.log(`[phase2] ✅ Login successful! ${cookies.length} cookies save → ${COOKIES_FILE}`);
    await browser.close();
    process.exit(0);

  } catch (err) {
    console.error('[phase2] ERROR:', err.message);
    await browser.close().catch(() => {});
    process.exit(1);
  }
})();
