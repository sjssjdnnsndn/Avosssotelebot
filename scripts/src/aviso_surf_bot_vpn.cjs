/**
 * AVISO.BZ — Surf + Like + Subscribe + Watch Bot  v10
 *
 * Task types:
 *   surf     → tasks-surf   → go-serf → confirm-serf
 *   like     → tasks-youtube → youtube-start-like.php → confirm button
 *   subscribe→ tasks-youtube → sub popup → podp-status
 *   watch    → tasks-youtube → start span → popup (hCaptcha) → Gemini solve →
 *              page reload → wait view timer → parent page reload → confirm
 *
 * Watch flow (v10):
 *   1. Click start span → aviso opens create-task-view.php popup
 *   2. Popup shows hCaptcha — solve with Gemini vision
 *   3. captcha.php called → popup reloads → server marks session valid
 *   4. Wait the video view timer (task.timer + 10s buffer)
 *   5. Close popup → reload parent tasks-youtube page
 *   6. Real report_id / hash now in DOM → click confirm → balance credited
 *
 * Loop: 24x7 persistent, no long breaks. 40-50s sleep when no tasks.
 */

'use strict';

const { chromium }       = require('playwright');
const { solveHCaptcha }  = require('./captcha_solver.cjs');
const https = require('https');
const http  = require('http');
const fs    = require('fs');
const path  = require('path');
const { doLogin, COOKIES_FILE, VPN_BYPASS_SCRIPT } = require('./aviso_login.cjs');

// ── CONFIG ─────────────────────────────────────────────────────────────────────
const CHROMIUM_NIX = '/nix/store/qa9cnw4v5xkxyip6mb9kxqfq1z4x2dx1-chromium-138.0.7204.100/bin/chromium';
const CHROMIUM_SYS = '/run/current-system/sw/bin/chromium';
const CHROMIUM = fs.existsSync(CHROMIUM_NIX) ? CHROMIUM_NIX
               : fs.existsSync(CHROMIUM_SYS) ? CHROMIUM_SYS
               : undefined;

const LOG_FILE    = path.join(__dirname, 'aviso_surf_log.txt');
const STATUS_FILE = path.join(__dirname, 'aviso_status.json');

const LIKE_DWELL_MIN = 8000;
const LIKE_DWELL_MAX = 18000;

// Sleep when no tasks found — 1 minute then recheck
const SHORT_SLEEP_MIN = 40 * 1000;
const SHORT_SLEEP_MAX = 50 * 1000;

// Surf task human delays
const SURF_DELAYS = [60, 90, 2, 60];
let surfDelayIdx  = 0;
function nextSurfDelay() {
  const base = SURF_DELAYS[surfDelayIdx++ % SURF_DELAYS.length];
  return Math.max(1, Math.round(base * (1 + (Math.random() * 0.5 - 0.25)))) * 1000;
}

function randBetween(min, max) {
  return Math.floor(min + Math.random() * (max - min));
}

// ── State ──────────────────────────────────────────────────────────────────────
let state = {
  status: 'starting', currentTask: null,
  balance: '?', balanceRaw: 0,
  totalEarned: 0, totalTasks: 0,
  totalYtDone: 0, totalYtEarned: 0,
  sessionStart: Date.now(), sleepUntil: null,
  lastUpdated: new Date().toISOString(),
  log: [], balanceHistory: [],
};

const PUBLISHED_API = process.env.API_BASE_URL || 'https://file-saver-bot--bmg74jk7f3.replit.app';

function pushToApi(pathUrl, body) {
  return new Promise(resolve => {
    const data = JSON.stringify(body);
    const url  = new URL(pathUrl, PUBLISHED_API);
    const isHttps = url.protocol === 'https:';
    const client  = isHttps ? https : http;
    const port    = url.port ? Number(url.port) : (isHttps ? 443 : 80);
    const req  = client.request({
      hostname: url.hostname, port, path: url.pathname, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
      timeout: 5000,
    }, res => {
      res.resume();
      res.on('end', () => {
        if (res.statusCode && res.statusCode >= 400) {
          console.error(`[API] push failed — HTTP ${res.statusCode}`);
        }
        resolve();
      });
    });
    req.on('timeout', () => {
      console.error('[API] push timed out — aborting request');
      req.destroy();
      resolve();
    });
    req.on('error', (err) => {
      console.error(`[API] push error: ${err.message}`);
      resolve();
    });
    req.write(data); req.end();
  });
}

function saveStatus() {
  try { fs.writeFileSync(STATUS_FILE, JSON.stringify(state, null, 2)); } catch(_) {}
  pushToApi('/api/aviso/update', state).catch(() => {});
}

let lastHourSnap = Date.now();
function maybeSnapshotBalance() {
  if (Date.now() - lastHourSnap < 3600000) return;
  lastHourSnap = Date.now();
  state.balanceHistory = [...state.balanceHistory, {
    time: new Date().toISOString(),
    balance: state.balanceRaw,
    earned : state.totalEarned + state.totalYtEarned,
    tasks  : state.totalTasks  + state.totalYtDone,
  }].slice(-48);
  saveStatus();
}

const sleep = ms => new Promise(r => setTimeout(r, ms));
const ts    = () => new Date().toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' });

function log(msg) {
  const line = `[${ts()}] ${msg}`;
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch(_) {}
  state.log = [line, ...state.log].slice(0, 50);
  state.lastUpdated = new Date().toISOString();
  saveStatus();
}

function bar(pct, w = 22) {
  const f = Math.round(Math.min(pct, 1) * w);
  return '[' + '█'.repeat(f) + '░'.repeat(w - f) + ']';
}

async function timerWait(totalMs, label) {
  const step = 1000;
  let elapsed = 0;
  while (elapsed < totalMs) {
    const remaining = Math.ceil((totalMs - elapsed) / 1000);
    process.stdout.write(`\r   ⏱  ${bar(elapsed / totalMs)} ${remaining}s  ${label.substring(0, 30)}   `);
    await sleep(step);
    elapsed += step;
  }
  process.stdout.write(`\r   ✅ ${bar(1)} Done!                                               \n`);
}

// ── Balance ────────────────────────────────────────────────────────────────────
async function getBalance(page) {
  try {
    return await page.evaluate(() => {
      const el = document.querySelector('a.main-account__link, .main-account__link');
      return el ? el.innerText.trim().replace(/\s+/g, ' ') : '?';
    });
  } catch(_) { return '?'; }
}

// ── Session check ──────────────────────────────────────────────────────────────
async function isLoggedIn(page) {
  return page.evaluate(() => {
    if (/Выход|Logout|Баланс|Кабинет/i.test(document.body.innerText)) return true;
    if (document.querySelector('a.main-account__link,.main-account__link,[class*="account__link"]')) return true;
    const p = window.location.pathname;
    if (p.includes('login') || p.includes('register')) return false;
    if (p.includes('tasks') || p.includes('cabinet') || p === '/') return true;
    return false;
  });
}

// ── HTTP helpers ───────────────────────────────────────────────────────────────
function httpPost(urlPath, body, cookieHeader, referer = 'https://aviso.bz/tasks-surf') {
  return new Promise((resolve, reject) => {
    const postData = new URLSearchParams(body).toString();
    const req = https.request({
      hostname: 'aviso.bz', port: 443, path: urlPath, method: 'POST',
      headers: {
        'Content-Type'    : 'application/x-www-form-urlencoded; charset=UTF-8',
        'X-Requested-With': 'XMLHttpRequest',
        'Cookie'          : cookieHeader,
        'Referer'         : referer,
        'Origin'          : 'https://aviso.bz',
        'User-Agent'      : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
        'Content-Length'  : Buffer.byteLength(postData),
      }
    }, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch(_) { resolve({ raw: data.substring(0, 400) }); }
      });
    });
    req.on('error', reject);
    req.write(postData); req.end();
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  YOUTUBE TASK PARSING  (like + subscribe + watch)
// ══════════════════════════════════════════════════════════════════════════════
async function fetchAllYtTasks(page) {
  return page.evaluate(() => {
    const tasks = [];

    // LIKE tasks
    document.querySelectorAll('[id^="likes-link-"][data-status="inactive"]').forEach(el => {
      const id = el.id.replace('likes-link-', '');
      if (!id || isNaN(Number(id))) return;
      const startDiv = document.getElementById('start-likes-' + id);
      if (!startDiv) return;
      const span = startDiv.querySelector('[onclick*="likes-start"],[onclick*="start_youtube_like"]');
      if (!span) return;
      const oc = span.getAttribute('onclick') || '';
      const m  = oc.match(/start_youtube_like[^\(]*\((\d+),\s*'([^']+)'/);
      if (!m) return;
      const ytUrl = span.getAttribute('title') || '';
      let price = 0;
      const priceEl = el.querySelector('.ruble-symbol');
      if (priceEl) {
        const txt = priceEl.parentElement ? priceEl.parentElement.textContent : '';
        const pm  = txt.match(/([\d.]+)/);
        if (pm) price = parseFloat(pm[1]);
      }
      tasks.push({ type: 'like', id, hash: m[2], ytUrl, price, priority: 2 });
    });

    // SUBSCRIBE tasks
    document.querySelectorAll('[id^="podp-link-"][data-status="inactive"]').forEach(el => {
      const id = el.id.replace('podp-link-', '');
      if (!id || isNaN(Number(id))) return;
      const startDiv = document.getElementById('start-podp-' + id);
      if (!startDiv) return;
      const span = startDiv.querySelector('[onclick*="podp-start"],[onclick*="start_youtube_subscribe"]');
      if (!span) return;
      const oc = span.getAttribute('onclick') || '';
      const m  = oc.match(/start_youtube_subscribe[^\(]*\((\d+),\s*'([^']+)'/);
      if (!m) return;
      let price = 0;
      const priceEl = el.querySelector('.ruble-symbol');
      if (priceEl) {
        const txt = priceEl.parentElement ? priceEl.parentElement.textContent : '';
        const pm  = txt.match(/([\d.]+)/);
        if (pm) price = parseFloat(pm[1]);
      }
      tasks.push({ type: 'subscribe', id, hash: m[2], price, priority: 1 });
    });

    // WATCH/VIDEO tasks — ads-link-{id}, confirm via viewCheckDirect
    document.querySelectorAll('[id^="ads-link-"][data-status="inactive"]').forEach(el => {
      const id = el.id.replace('ads-link-', '');
      if (!id || isNaN(Number(id))) return;
      const startDiv = document.getElementById('start-ads-' + id);
      if (!startDiv) return;

      // Hash from ANY start_youtube call (commented or live)
      const html = startDiv.innerHTML;
      const hashMatch = html.match(/start_youtube[^(]*\(\s*\d+\s*,\s*'([a-f0-9]+)'/);
      if (!hashMatch) return;
      const hash = hashMatch[1];

      // YouTube URL from the live start span title
      const startSpan = document.getElementById('link_ads_start_' + id)
                     || startDiv.querySelector('[title*="youtube.com"]');
      const ytUrl = startSpan ? (startSpan.getAttribute('title') || '') : '';

      // Timer in seconds from start_youtube_new call
      const timerMatch = html.match(/start_youtube_new\(\s*\d+\s*,\s*'(\d+)'/);
      const timer = timerMatch ? parseInt(timerMatch[1]) : 5;

      let price = 0;
      const priceEl = el.querySelector('.ruble-symbol');
      if (priceEl) {
        const txt = priceEl.parentElement ? priceEl.parentElement.textContent : '';
        const pm  = txt.match(/([\d.]+)/);
        if (pm) price = parseFloat(pm[1]);
      }

      tasks.push({ type: 'watch', id, hash, ytUrl, timer, price, priority: 3 });
    });

    tasks.sort((a, b) => (b.priority - a.priority) || (b.price - a.price));
    return tasks;
  });
}

// ══════════════════════════════════════════════════════════════════════════════
//  VPN POPUP handler
// ══════════════════════════════════════════════════════════════════════════════
async function openAndCheckPopup(context, url, dwellMs, taskLabel) {
  log(`  ${taskLabel} Opening popup: ${url.substring(0, 70)}`);
  const popPage = await context.newPage();
  let vpnDetected = false;
  try {
    await popPage.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });
    await popPage.waitForTimeout(2000);
    const popupText = await popPage.evaluate(() => document.body ? document.body.innerText : '');
    if (/vpn|proxy|прокси|обнаружен|запрещ|blocked|not allow/i.test(popupText)) {
      vpnDetected = true;
      log(`  ${taskLabel} ⚠ VPN detected → "${popupText.substring(0, 120)}"`);
    } else {
      log(`  ${taskLabel} ✅ No VPN warning. Dwelling ${Math.round(dwellMs/1000)}s`);
      await timerWait(dwellMs, taskLabel + ' dwell');
    }
  } catch(e) {
    log(`  ${taskLabel} Popup error: ${e.message}`);
  } finally {
    await popPage.close().catch(() => {});
  }
  return { vpnDetected };
}

// ══════════════════════════════════════════════════════════════════════════════
//  LIKE TASK
// ══════════════════════════════════════════════════════════════════════════════
async function runLikeTask(page, context, task) {
  const { id, ytUrl } = task;
  const lbl = `[like ${id}]`;
  log(`${lbl} Starting | YouTube: ${ytUrl.substring(0, 60)}`);
  state.currentTask = { type: 'like', id, ytUrl, startAt: new Date().toISOString() };
  saveStatus();

  try {
    // Step 1: Click start span → opens aviso like page
    const likePagePromise = page.waitForEvent('popup', { timeout: 15000 }).catch(() => null);
    await page.evaluate((taskId) => {
      window.__cfRLUnblockHandlers = true;
      const span = document.querySelector(`#start-likes-${taskId} [onclick*="likes-start"]`)
                || document.querySelector(`#start-likes-${taskId} [onclick*="start_youtube_like"]`)
                || document.querySelector(`[onclick*="start_youtube_like"][onclick*="${taskId}"]`);
      if (span) span.click();
      else {
        const any = document.querySelector(`#start-likes-${taskId} a, #start-likes-${taskId} span`);
        if (any) any.click();
      }
    }, id);
    await page.waitForTimeout(3000);

    // Step 2: Get aviso like-page
    let likePage = await likePagePromise;
    if (!likePage) {
      log(`${lbl} No popup event — opening like page directly`);
      likePage = await context.newPage();
      await likePage.goto(`https://aviso.bz/go/youtube-start-like.php?task_id=${id}`, {
        waitUntil: 'domcontentloaded', timeout: 20000,
      }).catch(e => log(`${lbl} Direct page error: ${e.message}`));
    } else {
      try { await likePage.waitForLoadState('domcontentloaded', { timeout: 10000 }); } catch(_) {}
    }
    log(`${lbl} Like page: ${likePage.url().substring(0, 80)}`);
    await likePage.waitForTimeout(2000);

    // VPN check
    const likeText = await likePage.evaluate(() => document.body ? document.body.innerText : '').catch(() => '');
    if (/vpn|proxy|прокси|обнаружен|запрещ|blocked|not allow/i.test(likeText)) {
      log(`${lbl} ⚠ VPN detected — skipping`);
      await likePage.close().catch(() => {});
      state.currentTask = null; saveStatus();
      return { success: false, skipped: true, reason: 'VPN' };
    }

    // Step 3: Click Like button → YouTube popup opens, close immediately
    log(`${lbl} Clicking Like button...`);
    const ytPopupPromise = context.waitForEvent('page', { timeout: 6000 }).catch(() => null);
    await likePage.evaluate(() => {
      window.__cfRLUnblockHandlers = true;
      const selectors = [
        'a[href*="youtube.com"]', 'a[href*="youtu.be"]',
        'a.btn-like', 'a.like-btn', 'a.like_btn', '.like-button a', '.like_button a',
        'a[target="_blank"]', 'a.btn', 'a.button',
      ];
      for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) { el.click(); return; }
      }
      const first = document.querySelector('a[href]');
      if (first) first.click();
    });

    // Step 4: Close YouTube popup immediately (≤2s)
    const ytPopup = await ytPopupPromise;
    if (ytPopup) {
      log(`${lbl} YouTube popup detected → closing immediately`);
      await sleep(600);
      await ytPopup.close().catch(() => {});
      log(`${lbl} YouTube popup closed ✅`);
    }
    await likePage.waitForTimeout(1000);
    await likePage.close().catch(() => {});
    log(`${lbl} Like page closed — checking for confirm`);

    // Step 5: Reload and find confirm button
    await page.waitForTimeout(2000);
    let confirmed = false;
    const startTs = Date.now();
    const maxWait = 35000;
    while (Date.now() - startTs < maxWait && !confirmed) {
      try { await page.reload({ waitUntil: 'domcontentloaded', timeout: 25000 }); } catch(_) {}
      await page.waitForTimeout(3000);
      await page.evaluate(() => { window.__cfRLUnblockHandlers = true; });

      const clickResult = await page.evaluate((taskId) => {
        const btn = document.querySelector(`#start-likes-${taskId} [onclick*="likes-status"]`)
                 || document.querySelector(`[data-id-task="${taskId}"][onclick*="likes-status"]`)
                 || document.querySelector(`[onclick*="likes-status"][onclick*="${taskId}"]`);
        if (btn) {
          const oc = btn.getAttribute('onclick') || '';
          if (!oc.includes('{REPORT_ID}') && !oc.includes('{HASH}')) {
            btn.style.display = 'inline-block'; btn.click();
            return { clicked: true, onclick: oc.substring(0, 60) };
          }
        }
        const sLink = document.querySelector(`.status-link-youtube[data-id-task="${taskId}"]`);
        if (sLink) { sLink.style.display = 'inline-block'; sLink.click(); return { clicked: true, method: 'status-link' }; }
        return { clicked: false };
      }, id);

      if (clickResult.clicked) {
        confirmed = true;
        log(`${lbl} ✅ Confirm clicked`);
      } else {
        log(`${lbl} Waiting for confirm... ${Math.round((Date.now()-startTs)/1000)}s`);
        await page.waitForTimeout(5000);
      }
    }

    if (!confirmed) {
      log(`${lbl} ❌ Confirm button never appeared`);
      state.currentTask = null; saveStatus();
      return { success: false, reason: 'confirm not found' };
    }

    await page.waitForTimeout(4000);
    const bal = await getBalance(page);
    state.balance = bal; state.balanceRaw = parseFloat(bal) || state.balanceRaw;
    state.totalYtDone++;
    state.currentTask = null;
    maybeSnapshotBalance(); saveStatus();
    log(`${lbl} ✅ Like complete | Balance: ${bal}`);
    return { success: true };

  } catch(err) {
    log(`${lbl} ❌ Error: ${err.message}`);
    state.currentTask = null; saveStatus();
    return { success: false, reason: err.message };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
//  SUBSCRIBE TASK
// ══════════════════════════════════════════════════════════════════════════════
async function runSubscribeTask(page, context, task) {
  const { id } = task;
  const lbl = `[sub ${id}]`;
  log(`${lbl} Starting subscribe task`);
  state.currentTask = { type: 'subscribe', id, startAt: new Date().toISOString() };
  saveStatus();

  try {
    const popupPromise = page.waitForEvent('popup', { timeout: 15000 }).catch(() => null);
    await page.evaluate((taskId) => {
      window.__cfRLUnblockHandlers = true;
      const span = document.querySelector(`#start-podp-${taskId} [onclick*="podp-start"]`)
                || document.querySelector(`[onclick*="start_youtube_subscribe"][onclick*="${taskId}"]`);
      if (span) span.click();
    }, id);
    await page.waitForTimeout(3500);

    let vpnDetected = false;
    const popup = await popupPromise;
    if (popup) {
      try { await popup.waitForLoadState('domcontentloaded', { timeout: 8000 }); } catch(_) {}
      const txt = await popup.evaluate(() => document.body ? document.body.innerText : '');
      vpnDetected = /vpn|proxy|прокси|обнаружен|запрещ|blocked|not allow/i.test(txt);
      if (!vpnDetected) {
        const dwell = randBetween(LIKE_DWELL_MIN, LIKE_DWELL_MAX);
        await timerWait(dwell, `${lbl} sub popup`);
      }
      await popup.close().catch(() => {});
    } else {
      const subUrl = `https://aviso.bz/go/youtube-sub.php?task_id=${id}`;
      const dwell  = randBetween(LIKE_DWELL_MIN, LIKE_DWELL_MAX);
      const res    = await openAndCheckPopup(context, subUrl, dwell, lbl);
      vpnDetected  = res.vpnDetected;
    }

    if (vpnDetected) { state.currentTask = null; saveStatus(); return { success: false, skipped: true, reason: 'VPN' }; }

    await page.waitForTimeout(5000);
    const clicked = await page.evaluate((taskId) => {
      window.__cfRLUnblockHandlers = true;
      // Strictly scoped to this task's confirm control — no cross-task selectors
      const span = document.querySelector(`.status-link-youtube[data-id-task="${taskId}"]`)
                || document.querySelector(`#start-podp-${taskId} [onclick*="podp-status"]`)
                || document.querySelector(`[data-id-task="${taskId}"][onclick*="podp-status"]`)
                || document.querySelector(`[onclick*="podp-status"][onclick*="${taskId}"]`);
      if (span) { span.style.display = 'inline-block'; span.click(); return true; }
      return false;
    }, id);

    await page.waitForTimeout(5000);
    const bal = await getBalance(page);
    state.balance = bal; state.balanceRaw = parseFloat(bal) || state.balanceRaw;
    state.totalYtDone++;
    state.currentTask = null;
    maybeSnapshotBalance(); saveStatus();
    log(`${lbl} ✅ Subscribe done (clicked=${clicked}) | Balance: ${bal}`);
    return { success: clicked };
  } catch(err) {
    log(`${lbl} ❌ Error: ${err.message}`);
    state.currentTask = null; saveStatus();
    return { success: false, reason: err.message };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
//  WATCH TASK — open video popup, wait timer, close, click viewCheckDirect
// ══════════════════════════════════════════════════════════════════════════════
async function runWatchTask(page, context, task) {
  const { id, ytUrl, timer } = task;
  const lbl   = `[watch ${id}]`;
  const waitS = Math.max(timer || 10, 10) + 10;   // task timer + 10s buffer
  log(`${lbl} Starting | YT: ${(ytUrl || '').substring(0, 55)} | Timer: ${timer}s`);
  state.currentTask = { type: 'watch', id, ytUrl, startAt: new Date().toISOString() };
  saveStatus();

  let popup = null;

  try {
    // ── Step 1: Click start span → popup opens (create-task-view.php) ──────
    const popupPromise = page.waitForEvent('popup', { timeout: 12000 }).catch(() => null);
    await page.evaluate((taskId) => {
      window.__cfRLUnblockHandlers = true;
      const span = document.getElementById('link_ads_start_' + taskId)
                || document.querySelector(`#start-ads-${taskId} [onclick*="start_youtube_new"]`);
      if (span) { span.click(); return 'clicked'; }
      return 'not_found';
    }, id);
    log(`${lbl} Start clicked — waiting for popup...`);

    popup = await popupPromise;
    if (!popup) {
      log(`${lbl} ⚠ No popup opened — skipping`);
      state.currentTask = null;
      saveStatus();
      return { success: false, reason: 'no_popup' };
    }
    log(`${lbl} Popup: ${popup.url().substring(0, 80)}`);

    // Wait for popup DOM to settle fully (hCaptcha JS is async — needs 3s+)
    try { await popup.waitForLoadState('domcontentloaded', { timeout: 10000 }); } catch(_) {}
    await popup.waitForTimeout(3500);

    // ── Step 2: Check what the popup is showing ────────────────────────────
    const popupState = await popup.evaluate(() => {
      const body = document.body ? document.body.innerText : '';
      return {
        vpnBlock:   /vpn|proxy|прокси|отключ/i.test(body),
        hasHCaptcha: !!(document.querySelector('iframe[src*="hcaptcha"]') ||
                        document.querySelector('[class*="hcaptcha"]') ||
                        document.getElementById('h-captcha')),
        bodySnippet: body.substring(0, 120),
      };
    }).catch(() => ({ vpnBlock: false, hasHCaptcha: false, bodySnippet: '' }));

    log(`${lbl} Popup state: vpn=${popupState.vpnBlock} captcha=${popupState.hasHCaptcha} text="${popupState.bodySnippet.replace(/\n/g,' ').substring(0,80)}"`);

    // ── Step 2a: VPN block → skip task ─────────────────────────────────────
    if (popupState.vpnBlock) {
      log(`${lbl} 🚫 VPN detected by aviso — watch task blocked. Skipping.`);
      await popup.close().catch(() => {});
      state.currentTask = null;
      saveStatus();
      return { success: false, reason: 'vpn_block' };
    }

    // ── Step 2b: Solve hCaptcha if present ─────────────────────────────────
    if (popupState.hasHCaptcha) {
      log(`${lbl} hCaptcha detected → solving with Gemini...`);
      const solved = await solveHCaptcha(popup, (msg) => log(msg));

      if (!solved) {
        log(`${lbl} ❌ Captcha not solved — skipping task`);
        await popup.close().catch(() => {});
        state.currentTask = null;
        saveStatus();
        return { success: false, reason: 'captcha_failed' };
      }

      log(`${lbl} ✅ Captcha solved — waiting for popup to reload...`);
      // After captcha, popup reloads. Wait for it — then check for VPN again.
      try {
        await popup.waitForLoadState('domcontentloaded', { timeout: 10000 });
      } catch(_) {}
      await popup.waitForTimeout(2000);

      const afterCaptcha = popup.url().substring(0, 80);
      log(`${lbl} Popup after captcha: ${afterCaptcha}`);
    } else {
      log(`${lbl} No captcha on popup — proceeding directly`);
    }

    // ── Step 3: Wait in popup for server to count the view ─────────────────
    // DO NOT reload parent page — aviso's JS must update hidden inputs via
    // polling while popup stays open. Reload would reset all JS state.
    log(`${lbl} Waiting ${waitS}s in popup for server to register view...`);
    await popup.waitForTimeout(waitS * 1000);

    // ── Step 4: Check if parent page JS has populated real report_id/hash ──
    const domBefore = await page.evaluate((taskId) => {
      const r = document.getElementById('ads_report_id_' + taskId);
      const h = document.getElementById('ads_hash_' + taskId);
      return { report_id: r ? r.value : null, hash: h ? h.value : null };
    }, id).catch(() => ({ report_id: null, hash: null }));

    log(`${lbl} Parent DOM (before popup close): report_id="${domBefore.report_id}"`);

    // ── Step 5: Close popup ─────────────────────────────────────────────────
    await popup.close().catch(() => {});
    log(`${lbl} Popup closed`);
    popup = null;

    await page.waitForTimeout(1500);

    // ── Step 6: Unhide confirm container + click confirm button ─────────────
    const clicked = await page.evaluate((taskId) => {
      window.__cfRLUnblockHandlers = true;
      const container = document.getElementById('ads_checking_btn_' + taskId);
      if (container) {
        container.style.display      = 'block';
        container.style.visibility   = 'visible';
      }
      const btn = document.getElementById('ads_btn_confirm_' + taskId)
               || document.querySelector(`[data-id-task="${taskId}"].status-link-youtube`);
      if (btn) { btn.click(); return true; }
      return false;
    }, id);

    log(`${lbl} Confirm clicked: ${clicked}`);

    // ── Step 7: Wait for server, then check balance ─────────────────────────
    await page.waitForTimeout(5000);
    const bal = await getBalance(page);
    state.balance    = bal;
    state.balanceRaw = parseFloat(bal) || state.balanceRaw;

    // Only count earnings when confirm was actually sent to server
    if (clicked) {
      state.totalYtDone++;
      state.totalYtEarned += (task.price || 0);
    }

    state.currentTask = null;
    maybeSnapshotBalance();
    saveStatus();
    log(`${lbl} ✅ Watch done | confirmed=${clicked} | Balance: ${bal}`);
    return { success: clicked };

  } catch(err) {
    log(`${lbl} ❌ Error: ${err.message}`);
    if (popup) { await popup.close().catch(() => {}); }
    state.currentTask = null;
    saveStatus();
    return { success: false, reason: err.message };
  }
}

// ══════════════════════════════════════════════════════════════════════════════
//  SURF TASK — parse + run one task
// ══════════════════════════════════════════════════════════════════════════════
async function parseSurfTasks(page) {
  await page.goto('https://aviso.bz/tasks-surf', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3500);
  return page.evaluate(() => {
    const result = [];
    document.querySelectorAll('a.start-surfing-btn').forEach(btn => {
      let container = btn.parentElement;
      for (let i = 0; i < 8; i++) {
        if (!container) break;
        if (container.tagName === 'TABLE' || container.classList.contains('work-serf')) break;
        container = container.parentElement;
      }
      let price = 0;
      const symEl = container ? container.querySelector('.ruble-symbol') : null;
      if (symEl) {
        let node = symEl.previousSibling;
        while (node) {
          const t = (node.textContent || '').trim();
          if (t && !isNaN(parseFloat(t))) { price = parseFloat(t); break; }
          node = node.previousSibling;
        }
        if (!price) {
          const raw = symEl.parentNode.textContent.replace(symEl.textContent, '').replace(/\s/g, '');
          price = parseFloat(raw) || 0;
        }
      }
      if (price < 0.01) return;
      result.push({
        surfingId: btn.dataset.surfingId || '',
        hash     : btn.dataset.hash || '',
        timer    : parseInt(btn.dataset.timer) || 30,
        url      : btn.dataset.url || btn.title || '',
        price,
      });
    });
    result.sort((a, b) => b.price - a.price);
    return result;
  });
}

async function runOneSurfTask(page, context, task, tabId, cookieHeader) {
  const lbl = `[surf ${task.surfingId}]`;
  log(`${lbl} 💰${task.price} руб | ⏱${task.timer}s | ${task.url.substring(0, 45)}`);
  state.currentTask = { type: 'surf', id: task.surfingId, price: task.price, timer: task.timer };
  saveStatus();

  try {
    const startRes = await httpPost(
      '/ajax/earnings/ajax-serf.php',
      { id: task.surfingId, hash: task.hash, func: 'go-serf', auto_redirect: 'true', tabId },
      cookieHeader
    );
    const startOk = startRes.success !== false;
    log(`${lbl} go-serf: ${startOk ? '✅' : '❌ ' + JSON.stringify(startRes).substring(0, 60)}`);
    if (!startOk) { state.currentTask = null; saveStatus(); return false; }

    let targetPage = null;
    if (task.url && task.url.startsWith('http')) {
      targetPage = await context.newPage();
      targetPage.goto(task.url, { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
    }

    await timerWait((task.timer + 2) * 1000, task.url || 'surf');
    await page.bringToFront();

    const confirmRes = await httpPost(
      '/ajax/earnings/serf_status.php',
      { id: task.surfingId, func: 'confirm-serf', hash: task.hash, tabId },
      cookieHeader
    );
    if (targetPage) await targetPage.close().catch(() => {});

    const earnMatch = (confirmRes.message || '').match(/>([\d.]+)<\/b>/);
    const earned    = earnMatch ? parseFloat(earnMatch[1]) : 0;

    if (confirmRes.success) {
      state.totalTasks++;
      state.totalEarned = Math.round((state.totalEarned + earned) * 1000) / 1000;
      // Reload tasks page so DOM reflects server-credited balance
      await page.goto('https://aviso.bz/tasks-surf', { waitUntil: 'domcontentloaded', timeout: 15000 }).catch(() => {});
      await page.waitForTimeout(1200);
      const freshBal = await getBalance(page);
      state.balance  = freshBal;
      state.balanceRaw = parseFloat(freshBal) || state.balanceRaw;
      maybeSnapshotBalance();
      log(`${lbl} ✅ PAID +${earned} руб | Total: ${state.totalEarned} (${state.totalTasks}) | Balance: ${state.balance}`);
    } else {
      const err = (confirmRes.message || JSON.stringify(confirmRes)).replace(/<[^>]*>/g, '').trim().substring(0, 80);
      log(`${lbl} ❌ Not confirmed: ${err}`);
    }
  } catch(err) {
    log(`${lbl} 💥 ERROR: ${err.message}`);
    await sleep(5000);
  }
  state.currentTask = null; saveStatus();
  return true;
}

// ══════════════════════════════════════════════════════════════════════════════
//  MAIN UNIFIED LOOP
//  Runs inside one browser session persistently — no long breaks.
//  Returns 'session-expired' if logged out, or 'crash' on error.
// ══════════════════════════════════════════════════════════════════════════════
async function runMainLoop(page, context) {
  const tabId     = 'tab_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
  const skipYtIds = new Set();   // tasks to skip this session (VPN or repeated failure)
  const ytFailCnt = new Map();   // failure count per task id

  log(`\n🔄 Main loop started — running 24x7, no scheduled breaks`);
  state.status = 'working';
  saveStatus();

  while (true) {
    const elapsedMin = Math.round((Date.now() - Date.now()) / 60000);
    log(`\n── [${elapsedMin}m / 360m] Checking for tasks...`);

    let didWork = false;

    // ── 1. CHECK YOUTUBE TASKS ───────────────────────────────────────────────
    try {
      await page.goto('https://aviso.bz/tasks-youtube', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(3000);

      const loggedIn = await isLoggedIn(page);
      if (!loggedIn) {
        log('⚠ Session expired');
        return 'session-expired';
      }

      await page.evaluate(() => { window.__cfRLUnblockHandlers = true; });
      const allYtTasks = await fetchAllYtTasks(page);
      const ytTasks    = allYtTasks.filter(t => !skipYtIds.has(t.id) && t.type !== 'watch');

      const likeCt  = ytTasks.filter(t => t.type === 'like').length;
      const subCt   = ytTasks.filter(t => t.type === 'subscribe').length;
      const watchCt = ytTasks.filter(t => t.type === 'watch').length;

      if (ytTasks.length > 0) {
        log(`📋 YT tasks available: like=${likeCt} subscribe=${subCt} watch=${watchCt}`);
        const task = ytTasks[0];
        log(`▶ Running [${task.type}] id=${task.id} price=${task.price}`);

        let result;
        if      (task.type === 'like')      result = await runLikeTask(page, context, task);
        else if (task.type === 'subscribe') result = await runSubscribeTask(page, context, task);
        else if (task.type === 'watch')     result = await runWatchTask(page, context, task);
        else                                result = { success: false, reason: 'unknown' };

        if (result.success) {
          // Successful — clear any prior failure count
          ytFailCnt.delete(task.id);
        } else {
          // Failed — track consecutive failures; skip after 2
          const fails = (ytFailCnt.get(task.id) || 0) + 1;
          ytFailCnt.set(task.id, fails);
          if (fails >= 2 || (result.skipped && result.reason === 'VPN')) {
            log(`▶ Skipping task ${task.id} for this session (failures=${fails})`);
            skipYtIds.add(task.id);
          }
        }

        const fc = await context.cookies();
        fs.writeFileSync(COOKIES_FILE, JSON.stringify(fc, null, 2));

        await page.waitForTimeout(3000 + Math.random() * 3000);
        didWork = true;
        continue; // immediately re-check after each task
      } else {
        log(`📋 YT: no like/subscribe tasks available`);
      }
    } catch(e) {
      log(`⚠ YT check error: ${e.message}`);
    }

    // ── 2. CHECK SURF TASKS ──────────────────────────────────────────────────
    try {
      const surfTasks = await parseSurfTasks(page);

      const loggedIn = await isLoggedIn(page);
      if (!loggedIn) {
        log('⚠ Session expired during surf check');
        return 'session-expired';
      }

      const bal = await getBalance(page);
      state.balance = bal; state.balanceRaw = parseFloat(bal) || state.balanceRaw;
      maybeSnapshotBalance();

      if (surfTasks.length > 0) {
        log(`🌐 Surf tasks available: ${surfTasks.length}`);
        surfTasks.slice(0, 5).forEach((t, i) =>
          log(`   ${i+1}. 💰${t.price}руб | ⏱${t.timer}s | ${t.url.substring(0, 40)}`));

        let cookieHeader = (await context.cookies()).map(c => `${c.name}=${c.value}`).join('; ');

        for (let i = 0; i < surfTasks.length; i++) {
          await runOneSurfTask(page, context, surfTasks[i], tabId, cookieHeader);

          const fc = await context.cookies();
          fs.writeFileSync(COOKIES_FILE, JSON.stringify(fc, null, 2));
          cookieHeader = fc.map(c => `${c.name}=${c.value}`).join('; ');

          if (i < surfTasks.length - 1) {
            const d = nextSurfDelay();
            if (d > 5000) { log(`⏳ Human delay ${Math.ceil(d/1000)}s`); await timerWait(d, 'Human delay'); }
            else await sleep(d);
          }
        }
        didWork = true;
        continue; // re-check everything after surf batch
      } else {
        log(`🌐 Surf: no tasks available`);
      }
    } catch(e) {
      log(`⚠ Surf check error: ${e.message}`);
    }

    // ── 3. NOTHING FOUND — short sleep ───────────────────────────────────────
    if (!didWork) {
      const sleepMs  = randBetween(SHORT_SLEEP_MIN, SHORT_SLEEP_MAX);
      const sleepMin = Math.round(sleepMs / 60000);
      log(`\n😴 No tasks found — waiting ${sleepMin}min then rechecking...`);
      state.status     = 'waiting-for-tasks';
      state.sleepUntil = new Date(Date.now() + sleepMs).toISOString();
      saveStatus();
      await timerWait(sleepMs, `Waiting ${sleepMin}min for tasks`);
      state.sleepUntil = null;
      state.status     = 'working';
      saveStatus();
      log('🔍 Break over — rechecking tasks...');
    }
  }
}

// ══════════════════════════════════════════════════════════════════════════════
//  OUTER LOOP — handles login, browser lifecycle, long sleep
// ══════════════════════════════════════════════════════════════════════════════
(async () => {
  fs.appendFileSync(LOG_FILE, `\n${'═'.repeat(55)}\n=== BOT START v8 ${ts()} ===\n${'═'.repeat(55)}\n`);
  log('🚀 Aviso Bot v10 (like + subscribe + surf) starting…');
  log(`🛡  Chromium: ${CHROMIUM || 'bundled playwright'}`);
  log('🛡  Task types: like | subscribe | surf  [watch: DISABLED]');
  log(`♾️  Mode: 24x7 persistent — no long breaks ever`);
  log(`😴  No-task sleep: 1 min → recheck`);

  while (true) {
    // ── Load saved cookies (no fresh login — 2FA manual only) ─────────────
    let cookies = [];
    try {
      cookies = JSON.parse(fs.readFileSync(COOKIES_FILE, 'utf8'));
      log(`\n🍪 Loaded ${cookies.length} saved cookies — launching browser`);
    } catch(e) {
      log(`❌ Could not read cookies file: ${e.message} — retry in 30s`);
      await sleep(30000);
      continue;
    }

    // ── Launch browser (with retry on transient startup failures) ──────────
    let browser, context, page;
    let launchAttempt = 0;
    while (!browser) {
      launchAttempt++;
      try {
        browser = await chromium.launch({
          executablePath: CHROMIUM,
          args: [
            '--no-sandbox', '--disable-setuid-sandbox',
            '--enforce-webrtc-ip-permission-check',
            '--webrtc-ip-handling-policy=default_public_interface_only',
            '--disable-webrtc-hw-encoding',
            '--disable-webrtc-hw-decoding',
            '--disable-blink-features=AutomationControlled',
          ],
          headless: true,
        });

        context = await browser.newContext({
          userAgent : 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
          viewport  : { width: 1280, height: 800 },
          locale    : 'ru-RU',
          timezoneId: 'Europe/Moscow',
        });

        // VPN bypass injected into every page (including popups)
        await context.addInitScript(VPN_BYPASS_SCRIPT);
        context.on('page', async (newPage) => {
          try {
            await newPage.addInitScript(VPN_BYPASS_SCRIPT);
            newPage.on('domcontentloaded', async () => {
              try { await newPage.evaluate(() => { window.__cfRLUnblockHandlers = true; }); } catch(_) {}
            });
            log(`[VPN] ✅ Injected: ${newPage.url().substring(0, 50) || '(blank)'}`);
          } catch(e) { log(`[VPN] ⚠ ${e.message}`); }
        });

        await context.addCookies(cookies);
        page = await context.newPage();
        page.on('domcontentloaded', async () => {
          try { await page.evaluate(() => { window.__cfRLUnblockHandlers = true; }); } catch(_) {}
        });

      } catch(e) {
        log(`❌ Browser launch failed (attempt ${launchAttempt}): ${e.message}`);
        if (browser) { await browser.close().catch(() => {}); browser = null; }
        if (launchAttempt >= 3) { log('❌ 3 launch failures — waiting 5min before retry'); await sleep(5 * 60 * 1000); launchAttempt = 0; }
        else { await sleep(15000); }
      }
    }

    // Initial balance check
    try {
      await page.goto('https://aviso.bz/tasks-youtube', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(2000);
      const bal = await getBalance(page);
      state.balance    = bal;
      state.balanceRaw = parseFloat(bal) || 0;
      state.sessionStart = Date.now();
      log(`💰 Balance: ${bal}`);
    } catch(e) {
      log(`⚠ Initial load: ${e.message}`);
    }

    // ── Run main loop ──────────────────────────────────────────────────────
    let reason = 'unknown';
    try {
      reason = await runMainLoop(page, context);
    } catch(e) {
      log(`❌ Main loop crashed: ${e.message}`);
      reason = 'crash';
    }

    await browser.close().catch(() => {});
    log(`\n🔁 Loop ended (reason: ${reason})`);

    if (reason === 'session-expired') {
      log('⚠ Session expired — restarting with fresh login');
      await sleep(5000);
    } else {
      // crash or unknown — short wait before restart
      log('⚠ Unexpected exit — waiting 60s before restart');
      await sleep(60000);
    }
  }
})().catch(err => {
  const line = `[${ts()}] 💥 FATAL: ${err.message}`;
  console.error(line);
  try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch(_) {}
  state.status = 'error';
  saveStatus();
  process.exit(1);
});
