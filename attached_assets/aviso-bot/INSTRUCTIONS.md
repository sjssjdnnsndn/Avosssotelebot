# Aviso.bz Bot System — Rebuild Instructions

## System Overview

Yeh system 3 parts se bana hai:
1. **Aviso Bot** — Playwright browser bot jo Aviso.bz pe surf/like/subscribe tasks karta hai
2. **API Server** — Express.js server jo bot ka state store karta hai
3. **Dashboard** — React frontend jo live bot status dikhata hai

---

## CRITICAL: URL Ka Dhyan Rakho

**BOT ME HAMESHA PRODUCTION (PUBLISHED) URL USE KARO — DEV URL NAHI**

`scripts/src/aviso_surf_bot_vpn.cjs` mein yeh line hai:
```js
const PUBLISHED_API = process.env.API_BASE_URL || 'https://YOUR-APP-NAME--USERNAME.replit.app';
```

- ❌ **GALAT**: `https://abc123.pike.replit.dev` — yeh dev/temporary URL hai, band ho jaata hai
- ✅ **SAHI**: `https://file-saver-bot--bmg74jk7f3.replit.app` — yeh permanent production URL hai

**Published URL kaise milta hai?**
1. Replit mein app publish karo (Deploy button)
2. Deployment complete hone ke baad jo `.replit.app` URL mile woh use karo
3. `aviso_surf_bot_vpn.cjs` mein `PUBLISHED_API` update karo is URL se

---

## Step-by-Step Setup

### Step 1: Repository Structure

```
workspace/
├── artifacts/
│   ├── api-server/          # Express API server
│   │   └── src/routes/aviso.ts   # Bot state endpoints
│   └── aviso-dashboard/     # React dashboard
│       └── src/pages/dashboard.tsx
├── lib/
│   ├── api-spec/openapi.yaml      # API contract
│   └── api-client-react/          # Auto-generated hooks
└── scripts/src/
    ├── aviso_surf_bot_vpn.cjs    # MAIN BOT FILE
    ├── aviso_login.cjs            # Login helper
    ├── aviso_login_phase1.cjs     # Login step 1 (email+pass)
    ├── aviso_login_phase2.cjs     # Login step 2 (OTP)
    └── captcha_solver.cjs         # Captcha solver (stub)
```

### Step 2: Environment Secrets (Replit Secrets mein daalo)

| Secret Name       | Value                        |
|-------------------|------------------------------|
| AVISO_EMAIL       | Aviso.bz account email       |
| AVISO_PASS        | Aviso.bz account password    |
| SESSION_SECRET    | Koi bhi random string        |
| TELEGRAM_BOT_TOKEN| BotFather se mila token      |

### Step 3: Login Karo (Fresh Cookies)

```bash
# Phase 1: Email + Password bhejo
cd scripts/src && node aviso_login_phase1.cjs

# Phase 2: OTP code daalo (email se mila OTP)
cd scripts/src && node aviso_login_phase2.cjs
```

Yeh `aviso_cookies.json` create karega. **Cookies ~7 din mein expire hoti hain** — dobara login karna hoga.

### Step 4: API Server Setup

`artifacts/api-server/src/routes/aviso.ts` — in-memory state store hai (no DB needed):
- `GET /api/aviso/status` — bot ki current state return karta hai
- `POST /api/aviso/update` — bot yahan apna state push karta hai

### Step 5: Deploy Karo (PEHLE YEH KARO)

1. Replit mein **Publish/Deploy** karo
2. Production URL note karo (format: `https://APP-NAME--USERNAME.replit.app`)
3. `scripts/src/aviso_surf_bot_vpn.cjs` mein `PUBLISHED_API` update karo:
   ```js
   const PUBLISHED_API = 'https://YOUR-PRODUCTION-URL.replit.app';
   ```
4. Dobara Publish karo (taaki updated URL deployed version mein bhi jaaye)

### Step 6: Bot Start Karo

Replit workflows mein **"Aviso Bot"** workflow start karo:
```bash
cd scripts/src && node aviso_surf_bot_vpn.cjs
```

### Step 7: Dashboard Dekho

Production URL pe: `https://YOUR-APP.replit.app/aviso-dashboard/`

---

## Task Types

| Task      | Status   | Notes                              |
|-----------|----------|------------------------------------|
| surf      | ✅ Active | URL surfing, ~60-90s per task      |
| like      | ✅ Active | YouTube like, ~15-30s per task     |
| subscribe | ✅ Active | YouTube subscribe, ~30s per task   |
| watch     | ❌ Disabled | hCaptcha required — disabled       |

---

## Cookies Expire Ho Jaayein To

1. Bot workflow band karo
2. Re-login:
   ```bash
   cd scripts/src && node aviso_login_phase1.cjs
   # OTP email mein aayega
   cd scripts/src && node aviso_login_phase2.cjs
   ```
3. Bot workflow dobara start karo

---

## Common Errors

| Error | Fix |
|-------|-----|
| Bot offline dikhta hai dashboard pe | `PUBLISHED_API` check karo — dev URL na ho |
| Session expired | Re-login (Step 3) |
| 404 on `/api/aviso/status` | API server restart karo |
| Watch tasks fail | Normal hai — disabled hain |

---

## Stack

- Node.js 24, Playwright (Chromium)
- Express 5, TypeScript
- React + Vite + TanStack Query
- pnpm workspaces monorepo
