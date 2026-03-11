# InstaFlow — Developer Context

> **Read this first.** This file tells you everything you need to understand, extend, debug, and run this project. A senior engineer should need nothing else.

---

## What This System Does

**InstaFlow** is an Instagram automation engine. It performs one specific funnel:

1. Accept a list of Instagram reel URLs
2. For each reel: follow the creator → comment **"link"** (the creator's auto-DM trigger word)
3. Watch for the creator's automated DM response (which contains a landing page URL)
4. Extract and resolve the final URL from the DM (following all redirects)
5. Export every extracted link to a daily Excel report

**Why "link"?** Creators set up auto-DM bots (ManyChat, etc.) that fire when someone comments a specific keyword — almost universally `link`. Commenting it triggers a DM from the creator containing their CTA / landing page.

---

## System Architecture

```
Reel URL submitted (API)
    │
    ▼
process_reel (Celery worker)
    ├── resolve URL → media_id, creator_id
    ├── check follow status (Redis-cached)
    ├── delay log-normal(5.5s, σ1.5)   ← human simulation
    ├── follow creator
    ├── delay log-normal(14s, σ4)       ← simulate watching
    ├── comment "link"
    ├── like reel (30% probability)
    └── enqueue watch_dm (delay 5–30 min)
            │
            ▼
        watch_dm (Celery worker, self-re-enqueues)
            ├── poll DM thread every 5–8 min
            ├── scan messages for CTA keywords + URLs
            ├── if CTA detected → enqueue extract_link
            └── if no DM in 24h → mark expired, stop
                        │
                        ▼
                    extract_link (Celery, critical queue)
                        ├── Mode A: httpx redirect chase (80%)
                        ├── Mode B: Playwright browser fallback (20%)
                        ├── save final URL + redirect chain to PostgreSQL
                        └── export_excel (hourly Beat task) picks it up
```

---

## Project Structure

```
agent/
├── instaflow/
│   ├── config/
│   │   ├── settings.py          # ALL config via pydantic-settings from .env
│   │   ├── logging.py           # structlog JSON setup, rotating file handler
│   │   ├── comments.yaml        # comment_text: "link" (configurable)
│   │   └── cta_keywords.yaml    # CTA detection keywords + scoring weights
│   │
│   ├── core/                    # Pure business logic, no I/O
│   │   ├── session_vault.py     # Fernet-encrypted per-account session storage
│   │   ├── fingerprint.py       # Permanent device fingerprint per account
│   │   ├── health_monitor.py    # Score 0–100, drives normal/conservative/quarantine
│   │   ├── challenge_handler.py # IG challenge → pause → Telegram → operator code
│   │   └── proxy_manager.py     # Residential proxy pool, sticky assignment
│   │
│   ├── stealth/
│   │   ├── timing.py            # Log-normal delay generator (human simulation)
│   │   ├── comment_engine.py    # Returns "link" from comments.yaml
│   │   └── rate_limiter.py      # Redis sorted-set sliding window per action
│   │
│   ├── instagram/
│   │   ├── client.py            # instagrapi wrapper: vault + fingerprint + proxy
│   │   ├── follow.py            # Follow status check (Redis-cached) + follow action
│   │   ├── comment.py           # Post "link" comment + optional like
│   │   ├── dm_monitor.py        # Poll DMs, score for CTA, emit to extract_link
│   │   └── browser.py           # Playwright fallback for in-app link extraction
│   │
│   ├── storage/
│   │   ├── database.py          # Async SQLAlchemy engine + session factory
│   │   ├── models.py            # ORM: accounts, proxies, reels, dm_messages,
│   │   │                        #      extracted_links, follows, task_log
│   │   ├── redis_client.py      # Singleton async Redis + key namespace helpers
│   │   └── excel_exporter.py    # openpyxl daily + cumulative .xlsx output
│   │
│   ├── workers/
│   │   ├── celery_app.py        # Celery config, 3 queues, Beat schedule
│   │   ├── task_reel.py         # process_reel — main engagement pipeline
│   │   ├── task_dm.py           # watch_dm — self-re-enqueuing DM poller
│   │   ├── task_extract.py      # extract_link — dual-mode URL resolver
│   │   └── task_maintenance.py  # health_check, export_excel, recover_proxies,
│   │                            # check_follow_backs
│   │
│   ├── api/
│   │   ├── main.py              # FastAPI app factory + lifespan hooks
│   │   ├── websocket.py         # /ws/feed — live event broadcast
│   │   └── routes/
│   │       ├── reels.py         # POST /api/reels/submit, GET /api/reels
│   │       ├── account.py       # GET /api/account/health, /session
│   │       ├── links.py         # GET /api/links/extracted, /export
│   │       ├── control.py       # POST /api/control/pause|resume|challenge
│   │       └── stats.py         # GET /api/stats/summary
│   │
│   └── migrations/
│       └── env.py               # Alembic migration env wired to models
│
├── tests/
│   └── test_core.py             # Unit tests for core logic
├── exports/                     # Excel outputs (gitignored)
├── logs/                        # Rotating log files (gitignored)
├── vaults/                      # Encrypted session files (gitignored)
├── requirements.txt
├── .env.example                 # Template — copy to .env and fill in
├── alembic.ini
├── docker-compose.yml
└── Dockerfile
```

---

## Key Design Decisions

### 1. Comment is always "link"
The comment engine (`stealth/comment_engine.py`) always returns `"link"`. This is not a template system — it reads `comment_text` from `comments.yaml`. Configurable without code changes, but the value should stay `"link"` unless the creator's trigger word changes.

### 2. Session Vault (not password storage)
Passwords are never stored. On first login, instagrapi creates a session token which is Fernet-encrypted and written to `vaults/{username}_{id}.vault`. Every subsequent boot loads this token. If the session expires, the client re-logs in and saves a new token.

**Generate your encryption key:**
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. Device Fingerprint Consistency
Instagram tracks device fingerprint across sessions. `core/fingerprint.py` generates a fingerprint once per account (8 real Android devices in the catalog), stores it in the vault, and applies it on every session. **Never regenerate** the fingerprint for a live account.

### 4. Log-Normal Delays (not random.uniform)
`stealth/timing.py` uses `random.lognormvariate`. Humans are not uniformly random — log-normal produces realistic short delays with occasional longer ones. `random.uniform` is a bot signal.

### 5. Health Score → Operation Mode
Every API call can return negative signals (challenge, 429, blocked). The health score drives the operation mode:
- `≥ 70` → Normal (full rate limits)
- `40–69` → Conservative (limits halved)
- `< 40` → Quarantine (no actions, operator alerted)

### 6. Rate Limiter (Redis sliding window)
`stealth/rate_limiter.py` uses a Redis sorted set where `score = Unix timestamp`. To check a window: count entries in `[now - 3600, now]`. Each action type (follows, comments, dm_reads) has its own counter — hitting the comment limit doesn't block follows.

### 7. DM Scope Control
The DM monitor only watches threads from creators in the `watched_creators:{account_id}` Redis set. This set is populated only after commenting on their reel. Never scans all DMs — prevents processing unrelated messages.

### 8. Dual-Mode Link Extraction
- **Mode A** (`httpx`): Follows HTTP 301/302/307/308 redirects up to 15 hops. Captures the entire chain. Works for 80% of URLs.
- **Mode B** (`playwright`): Headless Chromium, intercepts network responses for the redirect chain. Used when Mode A fails or lands on the same URL (blocked redirect).

### 9. Celery Task Retry Backoff
`process_reel`: 3 retries, backoff 60s → 300s → 900s.
`watch_dm`: 5 retries. Also self-re-enqueues while polling (every ~5–8 min).
`extract_link`: 3 retries, 30s delay, on `critical` queue.

---

## Queues

| Queue | Tasks |
|---|---|
| `critical` | `extract_link`, challenge resolution |
| `default` | `process_reel`, `watch_dm` |
| `low` | `health_check`, `export_excel`, `recover_proxies`, `check_follow_backs` |

---

## Database Schema

| Table | Purpose |
|---|---|
| `accounts` | Instagram accounts: username, proxy, health_score, status |
| `proxies` | Residential proxy pool with lifecycle status |
| `reels` | Submitted reel URLs and their processing state |
| `dm_messages` | DMs received from watched creators, CTA scoring |
| `extracted_links` | Final resolved URLs with full redirect chain (JSONB) |
| `follows` | Follow relationships created by the bot |
| `task_log` | Full audit trail of every Celery task execution |

---

## Redis Key Namespace

```
session:{account_id}:state           → session metadata
ratelimit:{account_id}:{action}      → sorted set (sliding window)
seen_messages:{account_id}           → set of processed DM message IDs
watched_creators:{account_id}        → set of creator user IDs being watched
friendship_cache:{account_id}:{uid}  → cached friendship status (1h TTL)
health:{account_id}                  → health score
challenge:{account_id}:code          → operator-submitted challenge code
challenge:{account_id}:status        → challenge resolution state
```

---

## Excel Output

| File | Description |
|---|---|
| `exports/instaflow_export_YYYY-MM-DD.xlsx` | Daily rolling file |
| `exports/instaflow_ALL_TIME.xlsx` | Cumulative all-time file |

Columns: `#`, `Reel URL`, `Creator`, `DM Message`, `Original URL`, `Final URL`, `Redirect Hops`, `Method`, `Timestamp`

---

## How to Run (Local Development)

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ running on localhost:5432
- Redis 7+ running on localhost:6379

### Step 1 — Configure
```bash
cp .env.example .env
# Edit .env — fill in POSTGRES_PASSWORD and VAULT_ENCRYPTION_KEY at minimum
```

Generate `VAULT_ENCRYPTION_KEY`:
```bash
.venv/bin/python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Step 2 — Virtualenv + Install
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### Step 3 — Create DB
```bash
createdb instaflow   # or via psql
```

### Step 4 — Start Services (4 terminals)
```bash
# Terminal 1 — API
uvicorn instaflow.api.main:app --reload --port 8000

# Terminal 2 — Worker
celery -A instaflow.workers.celery_app worker -l info -Q critical,default,low -c 3

# Terminal 3 — Beat scheduler
celery -A instaflow.workers.celery_app beat -l info

# Terminal 4 — Flower (optional monitoring)
celery -A instaflow.workers.celery_app flower --port 5555
```

### Step 5 — Add Account + Submit Reels
```bash
# The API handles DB setup on startup (init_db)
# Add your first account directly via psql:
psql instaflow -c "INSERT INTO accounts(ig_username, country) VALUES('your_bot_account', 'IN');"

# Submit reels
curl -X POST http://localhost:8000/api/reels/submit \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://www.instagram.com/reel/XXXX/"], "account_id": 1}'
```

---

## How to Run (Docker)

```bash
cp .env.example .env
# Fill in .env

docker compose up -d
docker compose logs -f worker
```

- API: http://localhost:8000/docs
- Flower: http://localhost:5555
- Excel exports: `./exports/` (bind-mounted)

---

## Alerting (Telegram)

Fill in `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.
You get notified when:
- Health score drops below 40
- Challenge/CAPTCHA triggered
- Account quarantined
- Task goes to dead-letter queue
- Daily stats summary

---

## Safe Usage Limits

These are the conservative defaults in `.env`. **Do not increase without monitoring the account health score closely.**

| Action | Per Hour | Per Day |
|---|---|---|
| Follows | 10 | 50 |
| Comments | 12 | 60 |
| DM reads | 30 | 200 |

---

## Testing

```bash
.venv/bin/pytest tests/ -v
```

Tests in `tests/test_core.py` cover:
- Comment engine always returns `"link"`
- Log-normal delay floor and distribution
- Health monitor scoring, modes, multipliers
- CTA detector keyword + URL confidence scoring

---

## Common Issues

| Symptom | Likely Cause | Fix |
|---|---|---|
| `SessionVaultError: Failed to decrypt` | Wrong `VAULT_ENCRYPTION_KEY` | Regenerate key, delete old vault files |
| `LoginRequired` on startup | Session token expired | Delete vault file, bot will re-login |
| `RateLimitError` | Too many actions | Reduce rate limits in `.env`, check health score |
| `ChallengeRequired` | Instagram wants verification | POST to `/api/control/challenge/resolve` with the code |
| Account in quarantine | Score dropped below 40 | Check logs, fix underlying cause, manually reset score in DB |
| No DMs received | Creator uses different trigger word | Change `comment_text` in `comments.yaml` |
| Excel not updating | `export_excel` Beat task not running | Check Beat is running: `docker compose logs beat` |

---

## Phase Build Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Session vault, fingerprint, client bootstrap | ✅ Done |
| 2 | Follow status check + follow action + rate limiter | ✅ Done |
| 3 | Comment engine (always "link") + posting | ✅ Done |
| 4 | Celery workers + task orchestration + DB | ✅ Done |
| 5 | DM monitor + CTA detection + link resolver | ✅ Done |
| 6 | Excel exporter + Telegram alerts | ✅ Done |
| 7 | FastAPI control plane + WebSocket feed | ✅ Done |
| 8 | Proxy manager + health monitor + challenge handler | ✅ Done |
| 9 | Docker Compose + env config + tests + context docs | ✅ Done |
