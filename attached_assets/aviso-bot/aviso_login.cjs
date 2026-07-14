/**
 * Aviso.bz Login Module
 *
 * Can be used two ways:
 *   1. Run directly:  `node aviso_login.js`  → refreshes cookies file
 *   2. Import:        `const { doLogin } = require('./aviso_login');`
 *                      await doLogin();
 */

'use strict';

const { chromium } = require('playwright');
const fs   = require('fs');
const path = require('path');

const CHROMIUM_REPLIT = '/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium';
const CHROMIUM_CACHE  = '/home/runner/workspace/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell';
const CHROMIUM        = fs.existsSync(CHROMIUM_REPLIT) ? CHROMIUM_REPLIT
                      : fs.existsSync(CHROMIUM_CACHE)  ? CHROMIUM_CACHE
                      : undefined;

const COOKIES_FILE = path.join(__dirname, 'aviso_cookies.json');
const OTP_FILE     = path.join(__dirname, 'aviso_otp.txt');
const SITE_KEY     = '6Ldrp74UAAAAAJSgoce2L5YA6Ob8yF7yA1LvXPm9';
const EMAIL        = process.env.AVISO_EMAIL || 'snout-rut-silly@duck.com';
const PASS         = process.env.AVISO_PASS  || 'zazzawAtr@Sfa6';

async function doLogin() {
  console.log('[login] Starting fresh login…');

  const browser = await chromium.launch({
    executablePath: CHROMIUM,
    args    : ['--no-sandbox', '--disable-setuid-sandbox'],
    headless: true,
  });
  const context = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
  });
  const page = await context.newPage();

  try {
    // 1. Open login page
    console.log('[login] 1) Opening login page…');
    await page.goto('https://aviso.bz/login', { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000);

    // 2. Fill credentials
    console.log('[login] 2) Filling credentials…');
    await page.fill('input[name="username"]', EMAIL);
    await page.fill('input[name="password"]', PASS);

    // 3. Get reCAPTCHA v3 token
    console.log('[login] 3) Getting reCAPTCHA token…');
    const token = await page.evaluate(async (siteKey) => {
      return new Promise((resolve, reject) => {
        grecaptcha.ready(async () => {
          try { resolve(await grecaptcha.execute(siteKey, { action: 'login' })); }
          catch(e) { reject(e.message); }
        });
      });
    }, SITE_KEY);
    console.log('[login]    Token:', token ? token.substring(0, 40) + '…' : 'FAILED');

    await page.evaluate((t) => {
      document.querySelector('#g-recaptcha-response-v3').value = t;
      const s = document.querySelector('input[name="g-recaptcha-v3-sign"]');
      if (s) s.value = t;
    }, token);

    // 4. Click login
    console.log('[login] 4) Clicking login…');
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {}),
      page.click('#button-login')
    ]);
    console.log('[login]    URL after login:', page.url());

    // 5. Handle 2FA
    if (page.url().includes('2fa')) {
      console.log('[login] 5) 2FA detected — waiting for OTP…');
      console.log('[login]    >> OTP aane par ise file mein likho:', OTP_FILE);
      console.log('[login]    >> Ya seedha chat mein OTP bhejo');

      // Delete old OTP file if exists
      if (fs.existsSync(OTP_FILE)) fs.unlinkSync(OTP_FILE);

      // Poll for OTP file (wait up to 5 minutes)
      let otp = null;
      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        if (fs.existsSync(OTP_FILE)) {
          otp = fs.readFileSync(OTP_FILE, 'utf8').trim();
          if (otp) { fs.unlinkSync(OTP_FILE); break; }
        }
        await new Promise(r => setTimeout(r, 3000));
        process.stdout.write('.');
      }
      console.log('');

      if (!otp) throw new Error('OTP timeout — 5 minutes mein OTP nahi mila');

      console.log('[login]    OTP mila:', otp);
      await page.waitForTimeout(1500);

      const visibleInputs = await page.locator('input:not([type="hidden"])').all();
      if (visibleInputs.length > 0) {
        await visibleInputs[0].click();
        await visibleInputs[0].fill(otp);
      }

      await Promise.all([
        page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 20000 }).catch(() => {}),
        page.locator('button').first().click().catch(() => page.keyboard.press('Enter'))
      ]);
      console.log('[login]    URL after 2FA:', page.url());
    }

    // 6. Verify login by checking dashboard has "Выход" (Logout)
    const bodyText = await page.evaluate(() => document.body.innerText.substring(0, 500));
    const loggedIn = /Выход|Logout|Баланс|Кабинет/i.test(bodyText);
    if (!loggedIn) {
      console.log('[login] ⚠ Login verify FAILED. Body preview:', bodyText.substring(0, 200));
      throw new Error('Login failed - dashboard markers not found');
    }

    // 7. Save cookies
    const cookies = await context.cookies();
    fs.writeFileSync(COOKIES_FILE, JSON.stringify(cookies, null, 2));
    console.log(`[login] ✅ Session saved — ${cookies.length} cookies → ${COOKIES_FILE}`);
    return cookies;
  } finally {
    await browser.close().catch(() => {});
  }
}

// VPN bypass script injected into every page context
const VPN_BYPASS_SCRIPT = `
(function() {
  // Override WebRTC to prevent IP leak
  try {
    const origRTC = window.RTCPeerConnection || window.webkitRTCPeerConnection;
    if (origRTC) {
      window.RTCPeerConnection = function(config) {
        if (config && config.iceServers) config.iceServers = [];
        return new origRTC(config);
      };
      window.RTCPeerConnection.prototype = origRTC.prototype;
    }
  } catch(e) {}
  // Prevent navigator.connection leak
  try {
    Object.defineProperty(navigator, 'connection', { get: () => undefined });
  } catch(e) {}
})();
`;

module.exports = { doLogin, COOKIES_FILE, VPN_BYPASS_SCRIPT };

// Run directly if invoked as script
if (require.main === module) {
  doLogin().then(() => process.exit(0)).catch(err => {
    console.error('[login] FATAL:', err.message);
    process.exit(1);
  });
}
