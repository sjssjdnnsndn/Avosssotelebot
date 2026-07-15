# 🤖 REPLIT AI AGENT — SETUP INSTRUCTIONS

> **IMPORTANT:** These instructions are written specifically for the **Replit AI Agent**.
> Read this file completely before making any changes. The goal is to clone this
> repository and set it up correctly on a new Replit project — without modifying
> any existing code or logic.

---

## 📌 What This Project Does

This is a **24x7 automated surf/like/subscribe bot** for [aviso.bz](https://aviso.bz) — a Russian
paid-to-click platform. The bot:

- Opens tasks (surf, YouTube like/subscribe) using a headless Chromium browser (Playwright)
- Completes each task and confirms it to earn rubles
- Pushes live state (balance, task count, logs) to a local Express API server
- A Telegram bot reads that state and responds to `/status`, `/balance`, `/relogin` commands

### Architecture

```
aviso_surf_bot_vpn.cjs  ──POST /api/aviso/update──►  Express API (port 5000)
                                                            │
                                                      Telegram Bot
                                                      (reads state, responds to commands)
```

---

## 🗂️ Project Structure

```
/
├── artifacts/api-server/          ← Express API server + Telegram bot
│   └── src/
│       ├── app.ts                 ← Express app, /ping and / health routes
│       ├── index.ts               ← Server entry point
│       ├── routes/aviso.ts        ← Bot state store, /aviso/update, login phase1/phase2
│       └── telegram/bot.ts        ← Telegram bot (/status /balance /relogin commands)
│
├── scripts/src/                   ← Surf bot scripts (plain Node.js CJS)
│   ├── aviso_surf_bot_vpn.cjs     ← ⭐ Main surf bot (runs 24x7)
│   ├── aviso_login.cjs            ← One-shot login (saves cookies)
│   ├── aviso_login_phase1.cjs     ← Re-login phase 1 (called by /relogin Telegram command)
│   ├── aviso_login_phase2.cjs     ← Re-login phase 2 (OTP submit)
│   ├── captcha_solver.cjs         ← Captcha stub
│   ├── aviso_cookies.json         ← Session cookies (auto-managed by bot)
│   └── aviso_status.json          ← Bot state snapshot (auto-written by bot)
│
├── lib/api-spec/                  ← OpenAPI spec (reference only)
├── lib/api-zod/                   ← Auto-generated Zod types (reference only)
├── .replit                        ← Workflows, deployment config, env vars
├── pnpm-workspace.yaml            ← Workspace packages
└── package.json                   ← Root build script
```

---

## ⚙️ Step-by-Step Setup for Replit AI Agent

### STEP 1 — Create a New Replit Project

1. Go to [replit.com](https://replit.com) and create a **new Replit** project
2. Choose **"Import from GitHub"** and paste this repository URL
3. Replit will auto-detect it as a Node.js project

---

### STEP 2 — Install Dependencies

After import, open the Shell and run:

```bash
pnpm install
```

This installs all workspace dependencies across `artifacts/api-server`, `lib/api-spec`,
`lib/api-zod`, and `scripts`.

---

### STEP 3 — Set Environment Secrets

Go to **Replit → Secrets** (the lock icon in the left sidebar) and add these three secrets:

| Secret Name        | Value                          | Description                              |
|--------------------|--------------------------------|------------------------------------------|
| `AVISO_EMAIL`      | your aviso.bz email            | Login email for aviso.bz account         |
| `AVISO_PASS`       | your aviso.bz password         | Login password for aviso.bz account      |
| `TELEGRAM_BOT_TOKEN` | your Telegram bot token      | Get it from [@BotFather](https://t.me/BotFather) on Telegram |

> ⚠️ Never hardcode these values in any file. Always use Replit Secrets.

---

### STEP 4 — Configure `.replit` for Your Production URL

The `.replit` file already has the correct configuration. The only thing you need to update
is the **production API_BASE_URL** to match your new Replit deployment URL.

Open `.replit` and find this section at the bottom:

```toml
[userenv.development]
API_BASE_URL = "http://localhost:5000"

[userenv.production]
API_BASE_URL = "https://YOUR-REPLIT-APP-URL.replit.app"
```

Replace `YOUR-REPLIT-APP-URL` with your actual Replit deployment URL (ends in `.replit.app`).
You will get this URL after deploying in Step 7.

> ℹ️ In development (Replit workspace), the bot pushes state to `localhost:5000`.
> In production (deployed VM), it pushes to the live `.replit.app` URL.
> This switching is automatic — no code change needed.

---

### STEP 5 — First-Time Login (Save Cookies)

Before running the bot, you must log in to aviso.bz and save session cookies.

Open the **Shell** tab and run:

```bash
node scripts/src/aviso_login.cjs
```

This will:
- Open a headless Chromium browser
- Log in to aviso.bz using `AVISO_EMAIL` and `AVISO_PASS` from Secrets
- Save session cookies to `scripts/src/aviso_cookies.json`

If aviso.bz requires OTP/2FA:
```bash
# Phase 1 — triggers OTP SMS/email
node scripts/src/aviso_login_phase1.cjs

# Phase 2 — submit the OTP you received
node scripts/src/aviso_login_phase2.cjs YOUR_OTP_HERE
```

> ✅ You know it worked when `scripts/src/aviso_cookies.json` has 50+ cookies and
> the script exits with "Login successful".

---

### STEP 6 — Set Up Workflows in Replit

The `.replit` file already defines the correct workflows. They should appear automatically
after import. Verify these two workflows exist in **Replit → Workflows**:

#### Workflow 1: `Start application`
```
Command: PORT=5000 pnpm --filter @workspace/api-server run dev
```
This starts the Express API server + Telegram bot on port 5000.

#### Workflow 2: `Aviso Surf Bot`
```
Command: node scripts/src/aviso_surf_bot_vpn.cjs
```
This starts the surf bot. It runs 24x7 with no scheduled breaks.

> ▶️ To run both at once, click the **Run** button (it runs the "Project" workflow
> which launches both in parallel).

---

### STEP 7 — Deploy to Production (VM)

This project **must** be deployed as a **VM** (not Autoscale) because:
- The Telegram bot uses long-polling (persistent connection)
- The surf bot runs 24x7 with a persistent Chromium browser
- In-memory state is shared between the bot and API server

#### Deploy steps:
1. In Replit, go to **Deploy** tab
2. Select deployment type: **Reserved VM** (not Autoscale)
3. Build command is already set: `pnpm --filter @workspace/api-server run build`
4. Run command is already set: `PORT=5000 node --enable-source-maps artifacts/api-server/dist/index.mjs & node scripts/src/aviso_surf_bot_vpn.cjs & wait`
5. Click **Deploy**
6. Your production URL will be: `https://YOUR-APP-NAME--YOUR-USERNAME.replit.app`

After deployment, go back to Step 4 and update `.replit` with this production URL.

---

### STEP 8 — Verify the Ping Endpoint

Once deployed, test the ping endpoint to confirm the server is running:

```bash
curl https://YOUR-APP-NAME--YOUR-USERNAME.replit.app/ping
```

Expected response:
```json
{ "ok": true, "ts": 1720000000000, "message": "pong" }
```

---

### STEP 9 — Set Up Cron Job for Keep-Alive Ping

To keep the production server alive (prevent Replit from sleeping the VM), set up
an external cron job that pings `/ping` every 5 minutes.

#### Option A — cron-job.org (free)
1. Go to [cron-job.org](https://cron-job.org)
2. Create a new cron job
3. URL: `https://YOUR-APP-NAME--YOUR-USERNAME.replit.app/ping`
4. Schedule: every 5 minutes (`*/5 * * * *`)
5. Expected HTTP status: 200

#### Option B — UptimeRobot (free)
1. Go to [uptimerobot.com](https://uptimerobot.com)
2. Add new monitor → HTTP(S)
3. URL: `https://YOUR-APP-NAME--YOUR-USERNAME.replit.app/ping`
4. Monitoring interval: 5 minutes

#### Option C — GitHub Actions
Create `.github/workflows/ping.yml`:
```yaml
name: Keep Alive Ping
on:
  schedule:
    - cron: '*/5 * * * *'
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: curl -fs https://YOUR-APP-NAME--YOUR-USERNAME.replit.app/ping
```

---

## 📱 Telegram Bot Commands

Once everything is running, use your Telegram bot:

| Command     | What it does                                              |
|-------------|-----------------------------------------------------------|
| `/status`   | Shows bot status, balance, tasks completed today          |
| `/balance`  | Shows current aviso.bz balance                            |
| `/relogin`  | Triggers fresh login (if cookies expired, bot stops working) |

---

## 🔁 When Cookies Expire (Re-login Flow)

If the bot stops completing tasks (session expired), use Telegram:

1. Send `/relogin` to your Telegram bot
2. If aviso.bz needs OTP, bot will ask you to enter it
3. Send the OTP number (just the digits, e.g. `123456`)
4. Bot will confirm login and save fresh cookies automatically

---

## 🚀 Build Command Reference

```bash
# Install all dependencies
pnpm install

# Build API server (required before production deploy)
pnpm --filter @workspace/api-server run build

# Run API server in dev mode (port 5000)
PORT=5000 pnpm --filter @workspace/api-server run dev

# Run surf bot (separate terminal/workflow)
node scripts/src/aviso_surf_bot_vpn.cjs

# First-time login
node scripts/src/aviso_login.cjs

# Re-login with OTP
node scripts/src/aviso_login_phase1.cjs
node scripts/src/aviso_login_phase2.cjs YOUR_OTP
```

---

## ⚠️ Important Rules — DO NOT Change These

1. **Do NOT modify `package.json`** in the root or in any workspace package.
   Use `pnpm add` via Replit package tools to add dependencies.

2. **Do NOT change `deploymentTarget`** in `.replit` — it must stay `"vm"`.
   Autoscale deployments will NOT work (Telegram polling requires persistent connection).

3. **Do NOT add a frontend/dashboard** to this project without first separating it
   into its own artifact. This is a backend-only project.

4. **Do NOT change the port** from 5000 — it maps to external port 80 in `.replit`.

5. **Keep `scripts/src/aviso_cookies.json` out of Git** (it is already in `.gitignore`).
   These are live session credentials.

---

## 🩺 Health Check Endpoints

| URL                                              | Returns                          |
|--------------------------------------------------|----------------------------------|
| `GET /`                                          | `{ ok: true, service: "aviso-bot-server" }` |
| `GET /ping`                                      | `{ ok: true, ts: <timestamp>, message: "pong" }` |
| `GET /api/aviso/status`                          | Full bot state (balance, tasks, logs) |

---

## 📋 Tech Stack

| Layer       | Technology                          |
|-------------|-------------------------------------|
| Runtime     | Node.js 20, pnpm workspaces         |
| Browser     | Playwright + Chromium (headless)    |
| API Server  | Express 5, TypeScript, esbuild      |
| Telegram    | node-telegram-bot-api (polling)     |
| Validation  | Zod                                 |
| Logging     | pino, pino-http                     |
| Deployment  | Replit Reserved VM                  |
