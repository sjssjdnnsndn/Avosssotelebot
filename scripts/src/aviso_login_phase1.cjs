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
const SITE_KEY     = '6Ldrp74UAAAAAJSgoce2L5YA6Ob8yF7yA1LvXPm9';
const EMAIL        = process.env.AVISO_EMAIL;
const PASS         = process.env.AVISO_PASS;

if (!EMAIL || !PASS) {
  console.error('[phase1] AVISO_EMAIL / AVISO_PASS env vars missing');
  process.exit(1);
}

(async () => {
  console.log('[phase1] Browser launch kar raha hoon…');
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

  const page = await context.newPage();

  try {
    console.log('[phase1] Login page khol raha hoon…');
    await page.goto('https://aviso.bz/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000 + Math.random() * 1000);

    console.log('[phase1] Credentials fill kar raha hoon…');
    await page.fill('input[name="username"]', EMAIL);
    await page.waitForTimeout(500 + Math.random() * 500);
    await page.fill('input[name="password"]', PASS);
    await page.waitForTimeout(800 + Math.random() * 700);

    console.log('[phase1] reCAPTCHA token le raha hoon…');
    const token = await page.evaluate(async (sk) => {
      return new Promise((resolve) => {
        if (typeof grecaptcha === 'undefined') { resolve(null); return; }
        grecaptcha.ready(async () => {
          try { resolve(await grecaptcha.execute(sk, { action: 'login' })); }
          catch { resolve(null); }
        });
      });
    }, SITE_KEY);

    if (token) {
      await page.evaluate((t) => {
        const el = document.querySelector('#g-recaptcha-response-v3');
        if (el) el.value = t;
        const s = document.querySelector('input[name="g-recaptcha-v3-sign"]');
        if (s) s.value = t;
      }, token);
    }

    console.log('[phase1] Login button click…');
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {}),
      page.click('#button-login'),
    ]);

    const url = page.url();
    console.log('[phase1] Redirect URL:', url);

    if (url.includes('2fa')) {
      // Save partial cookies + 2FA URL for phase 2
      const cookies = await context.cookies();
      fs.writeFileSync(STATE_FILE, JSON.stringify({ twoFaUrl: url, cookies }, null, 2));
      console.log('[phase1] 2FA chahiye! State save ho gayi →', STATE_FILE);
      console.log('[phase1] NEED_OTP');
      await browser.close();
      process.exit(2); // exit code 2 = OTP needed
    }

    // No 2FA — already logged in
    const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
    const ok = /Выход|Logout|Баланс|Кабинет/i.test(bodyText);
    if (!ok) throw new Error('Login fail — dashboard markers nahi mile');

    const cookies = await context.cookies();
    fs.writeFileSync(COOKIES_FILE, JSON.stringify(cookies, null, 2));
    console.log('[phase1] Login ho gaya bina 2FA ke! Cookies save →', COOKIES_FILE);
    await browser.close();
    process.exit(0);

  } catch (err) {
    console.error('[phase1] ERROR:', err.message);
    await browser.close().catch(() => {});
    process.exit(1);
  }
})();
