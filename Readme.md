Here's the complete, final production-grade architecture document:

---

# **INSTAGRAM AUTOMATION PLATFORM — COMPLETE SYSTEM ARCHITECTURE**
### *For Coding Agent Consumption — Senior Architect Edition*

---

## **SYSTEM IDENTITY**

**Platform Name:** InstaFlow Automation Engine
**Purpose:** Automated reel engagement (follow + comment) with DM CTA extraction and Excel reporting
**Design Philosophy:** Stealth-first, stateful, fault-tolerant, fully observable
**Target:** Single secondary Instagram account operating safely at human-believable throughput

---

## **ARCHITECTURAL OVERVIEW**

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CONTROL PLANE                              │
│                                                                     │
│   ┌──────────────┐    ┌─────────────────┐    ┌──────────────────┐  │
│   │  Input Layer │───►│  FastAPI Server  │───►│   Task Router    │  │
│   │ (URLs / CLI) │    │   (REST + WS)   │    │  (Priority Queue)│  │
│   └──────────────┘    └─────────────────┘    └────────┬─────────┘  │
└────────────────────────────────────────────────────────┼────────────┘
                                                         │
┌────────────────────────────────────────────────────────▼────────────┐
│                        ORCHESTRATION LAYER                          │
│                                                                     │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │                    Celery Worker Pool                       │   │
│   │                                                             │   │
│   │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────┐  │   │
│   │  │ Follow Worker│ │Comment Worker│ │  DM Watch Worker   │  │   │
│   │  └──────┬───────┘ └──────┬───────┘ └─────────┬──────────┘  │   │
│   │         │                │                   │             │   │
│   │  ┌──────▼───────────────▼───────────────────▼──────────┐  │   │
│   │  │              Link Extractor Worker                   │  │   │
│   │  └──────────────────────────────────────────────────────┘  │   │
│   └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                          STEALTH LAYER                              │
│                                                                     │
│  ┌─────────────────┐  ┌──────────────────┐  ┌──────────────────┐   │
│  │  Session Vault  │  │  Proxy Rotator   │  │ Behavior Engine  │   │
│  │ (Fingerprints + │  │ (Sticky per      │  │ (Human-sim +     │   │
│  │  Cookies)       │  │  session)        │  │  Rate Limiter)   │   │
│  └─────────────────┘  └──────────────────┘  └──────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                      INSTAGRAM INTERFACE LAYER                      │
│                                                                     │
│         ┌──────────────────┐         ┌──────────────────┐          │
│         │   instagrapi     │         │   Playwright     │          │
│         │ (Primary: API)   │◄───────►│ (Fallback:Browser│          │
│         └──────────────────┘         └──────────────────┘          │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                          STORAGE LAYER                              │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  ┌──────────┐   │
│  │  PostgreSQL  │  │    Redis     │  │  Encrypted│  │  Excel   │   │
│  │  (State DB)  │  │(Queue+Cache) │  │  Secrets  │  │ Exporter │   │
│  └──────────────┘  └──────────────┘  └───────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## **MODULE 1 — SESSION & IDENTITY MANAGER**

**Responsibility:** Own the account's digital identity completely. A compromised session = dead account.

### 1.1 Session Vault
- Store per-account state in an encrypted JSON file using `cryptography.fernet`
- Vault contains: session cookies, device UUID, login timestamp, challenge history, last action timestamp
- Never store raw passwords anywhere on disk — only session tokens
- On every boot, load session from vault before any API call
- Persist updated session back to vault after every successful API response

### 1.2 Device Fingerprint Profile
- Each account gets a **permanently assigned** device profile generated once at account registration time
- Profile contains:
  - `user_agent`: Real Android/iOS UA string (pulled from a curated list of top devices)
  - `device_id`: Stable UUID4 generated once
  - `phone_id`: Stable UUID4
  - `timezone_offset`: Match your real timezone
  - `locale`: `en_US` or your real locale
  - `app_version`: Pinned to a real current Instagram app version
- This profile is passed to `instagrapi`'s settings on every session load
- **Never randomize fingerprint between sessions** — Instagram tracks device consistency

### 1.3 Account Health Monitor
- Maintain a `health_score` (integer 0–100) per account in PostgreSQL
- Score starts at 100, decrements on negative signals, increments on clean sessions
- Negative signals and their weights:

| Signal | Score Penalty |
|---|---|
| Checkpoint challenge received | -20 |
| Rate limit (429) hit | -10 |
| Login failure | -15 |
| CAPTCHA triggered | -25 |
| Action blocked response | -15 |

- Positive signals: +2 per clean session, +1 per successful comment
- Thresholds:
  - Score ≥ 70: Normal operation
  - Score 40–69: Conservative mode (halve all rate limits)
  - Score < 40: Quarantine — pause all activity, send alert to operator

### 1.4 Challenge Resolution Handler
- `instagrapi` provides challenge hooks — wire them to a handler function
- On challenge detected:
  1. Pause the task immediately, save task state to Redis
  2. Send a Telegram bot notification to operator with challenge type and account name
  3. Poll Redis for operator-provided resolution code (SMS code / email code) with 10-minute timeout
  4. Submit resolution code via instagrapi challenge API
  5. Resume task from saved state if successful
  6. If timeout: quarantine account, mark task as `CHALLENGE_TIMEOUT`

---

## **MODULE 2 — STEALTH & ANTI-DETECTION ENGINE**

**Responsibility:** Make every action indistinguishable from a real human using Instagram casually.

### 2.1 Behavioral Timing Engine
- All delays drawn from a **log-normal distribution** — NOT `random.uniform()`. Humans are not uniformly random. Log-normal produces realistic short delays with occasional longer ones.
- Parameter reference:

| Action | Mean Delay | Std Dev | Distribution |
|---|---|---|---|
| Between reel open and follow | 3–8s | 1.5s | log-normal |
| Between follow and comment | 8–20s | 4s | log-normal |
| Between two reel jobs | 45–180s | 30s | log-normal |
| Between DM poll cycles | 4–7 min | 1 min | log-normal |
| Session start warm-up | 60–120s idle | — | fixed range |

- Simulate pre-action "reading" behavior: open reel → wait → scroll → wait → act
- Never perform two actions back-to-back with zero delay

### 2.2 Comment Variation Engine
- Maintain a YAML config file: `comments.yaml` containing 50+ comment templates
- Templates use variable slots: `"Love this {noun}! {emoji}"`, `"This is {adjective} 🔥"`
- Synonym banks for each slot type stored alongside templates
- Rotation rules:
  - Track last 100 used comment+variant combinations in Redis
  - Never reuse same combination within 48-hour window
  - Shuffle emoji count (0–3 emojis per comment, randomized positions)
- Optional LLM enhancement: call a local `ollama` instance (llama3 / mistral) with reel caption text to generate a contextually relevant comment — use this for 20% of comments to appear genuine

### 2.3 Rate Limiter (Redis Sliding Window)
- Implemented as a Redis sorted set with timestamp-based sliding window
- Hard limits enforced at worker level — tasks self-throttle before hitting API:

| Action | Per Hour | Per Day | Per Week |
|---|---|---|---|
| Follows | 10 | 50 | 200 |
| Comments | 12 | 60 | 250 |
| DM reads | 30 | 200 | unlimited |
| Profile views | 20 | 100 | unlimited |

- If any limit is reached: worker sleeps until window clears, does not drop the task
- Separate limit counters per action type — hitting comment limit doesn't block follows

### 2.4 Proxy Manager
- Maintain a pool of residential proxies in PostgreSQL (`proxies` table)
- Each account is assigned one sticky proxy at session start
- Proxy assignment rules:
  - Same proxy reused for entire session duration
  - Proxy must be from same country as account's original registration country
  - Proxy health checked before assignment: test with a lightweight HTTP call
- Proxy health lifecycle:
  - `active`: passing health checks, in rotation
  - `cooling`: used recently, in mandatory rest period
  - `degraded`: occasional failures, used only as fallback
  - `retired`: consistent failures, removed from pool
- Recommended providers: Smartproxy, Brightdata, or Oxylabs (residential, not datacenter)

### 2.5 Action Sequencer
Every reel engagement follows a strict, randomized-but-ordered sequence:

```
REEL RECEIVED
     │
     ▼
[1] Open reel media via API (fetch media_id from URL)
     │
     ▼
[2] Check follow status → instagrapi.user_friendship()
     │
     ├── Already following? ──► Skip to [5]
     │
     ▼
[3] Simulate "viewing" delay (3–8s log-normal)
     │
     ▼
[4] Follow creator → instagrapi.user_follow(user_id)
     │   Record follow in DB
     │   Update rate limiter
     │
     ▼
[5] Simulate "watching reel" delay (8–20s log-normal)
     │
     ▼
[6] Post comment → instagrapi.media_comment(media_id, text)
     │   Record comment in DB
     │   Update rate limiter
     │
     ▼
[7] Optionally like reel (30% probability) → human signal
     │
     ▼
[8] Mark reel job as COMPLETED
     │
     ▼
[9] Enqueue DM watch task for this creator (delayed 5–30 min)
```

---

## **MODULE 3 — FOLLOW MANAGEMENT SUBSYSTEM**

**Responsibility:** Determine follow status accurately and follow creators safely.

### 3.1 Follow Status Detection (Your Idea — Implemented Correctly)
- Your browser-based idea (check for Follow button) is valid for visual scraping, but the API approach is more reliable and faster
- **Preferred approach:** `instagrapi.user_friendship(user_id)` returns a friendship status object with fields:
  - `following: bool` — you follow them
  - `followed_by: bool` — they follow you
  - `blocking: bool`
  - `is_private: bool`
- If `following == False` → execute follow flow
- If `following == True` → skip follow, proceed directly to comment
- Cache friendship status in Redis with 1-hour TTL to avoid redundant API calls for repeated creators

### 3.2 Private Account Handling
- If `is_private == True` on friendship check:
  - Send follow request (same API call, Instagram handles it as a request)
  - Mark reel job as `AWAITING_FOLLOW_APPROVAL` in DB
  - Do NOT attempt to comment — commenting on private account reels you haven't been approved to see will fail
  - DM watch task still queued in case creator sees the follow request and DMed proactively
  - After 24h with no approval: mark as `FOLLOW_REQUEST_EXPIRED`, log and move on

### 3.3 Follow-Back Tracker
- After following a creator, record in `follows` table: `creator_username`, `followed_at`, `follow_back_status`
- Background job checks every 6 hours if followed creators have followed back
- Useful signal: follow-back = higher engagement creator, worth prioritizing

### 3.4 Unfollow Safety (Optional but Recommended)
- Instagram also penalizes aggressive follow/unfollow churn
- If you want cleanup: schedule optional unfollow for creators who haven't DMed within 7 days
- Unfollow rate capped at 5/hour, 20/day — separate rate limiter counter

---

## **MODULE 4 — TASK ORCHESTRATION**

**Responsibility:** Distribute, schedule, retry, and monitor all async work.

### 4.1 Celery Configuration
- Broker: Redis
- Result backend: Redis (with PostgreSQL mirroring for persistence)
- Workers: 3 concurrent worker processes (more is unnecessary and risky)
- Each worker handles one account — do not share workers across accounts

### 4.2 Task Definitions

**`task.process_reel(reel_url: str, account_id: int)`**
- Entry point for all reel jobs
- Resolves URL → media_id → creator user_id
- Runs the Action Sequencer (Module 2.5)
- On success: emits `task.watch_dm` with delay
- Retries: 3 attempts, exponential backoff (60s, 300s, 900s)
- On final failure: dead-letter queue + alert

**`task.watch_dm(creator_user_id: int, account_id: int, deadline: datetime)`**
- Polls DM thread with specific creator
- Scans messages for CTA keywords
- If CTA found: emits `task.extract_link`
- If `deadline` exceeded (24h): mark as `NO_DM_RECEIVED`, stop polling
- Poll interval: 5–8 minutes (randomized)

**`task.extract_link(dm_message_id: str, raw_url: str, account_id: int)`**
- Resolves the raw URL through all redirects
- If URL requires browser click (Instagram in-app links): spawns Playwright session
- Saves result to PostgreSQL
- Triggers Excel export

**`task.health_check(account_id: int)`**
- Runs every 30 minutes via Celery Beat
- Pings Instagram API with lightweight call (fetch own profile)
- Updates health score
- Logs session validity

**`task.export_excel()`**
- Runs every hour via Celery Beat
- Reads all new `extracted_links` records since last export
- Appends rows to master Excel file
- Also generates a fresh full export on demand via API endpoint

### 4.3 Queue Priority
- Three named queues in Redis: `critical`, `default`, `low`

| Task | Queue |
|---|---|
| Challenge resolution | critical |
| process_reel | default |
| watch_dm | default |
| extract_link | critical |
| health_check | low |
| export_excel | low |

### 4.4 Dead Letter Queue
- Failed tasks (exhausted retries) go to `dlq` queue
- DLQ processor runs every 15 minutes
- Sends Telegram notification with task ID, error, and stack trace
- Stores full failure context in PostgreSQL for manual review

---

## **MODULE 5 — DM MONITOR & CTA EXTRACTOR**

**Responsibility:** Watch for creator DMs, detect CTAs, resolve links.

### 5.1 DM Scope Control
- Do NOT monitor all DMs — only threads with creators from your `reels` table
- Maintain a `watched_creator_ids` set in Redis, populated after each successful comment
- This prevents accidentally processing unrelated DMs and keeps the system focused

### 5.2 CTA Detection Engine
- Fetch DM thread: `instagrapi.direct_messages(thread_id)`
- For each new message (not in `seen_message_ids` Redis set):
  - Run keyword matcher against message text
  - CTA keyword list (configurable in YAML):
    ```
    ["click", "tap here", "check this", "link", "join", "get", 
     "open", "visit", "follow this", "go to", "see here", "access"]
    ```
  - Also detect: message contains a URL pattern (regex)
  - Also detect: message contains an Instagram story reply with a link sticker
- Confidence scoring: messages with both keyword + URL pattern score highest
- Threshold: confidence ≥ 0.7 → trigger extraction

### 5.3 Link Resolver (Two-Mode)

**Mode A — Plain URL (80% of cases):**
- Use `requests.Session()` with the account's proxy
- Set `max_redirects=15`, follow all 301/302/meta-refresh
- Capture every hop in the redirect chain
- Return final URL + full chain array
- Timeout: 15 seconds per hop

**Mode B — In-App Button / Story Link (20% of cases):**
- Launch Playwright browser with account's persistent browser context
- Load the DM thread URL in the browser
- Wait for CTA element (button, link, story reply) to appear
- Click element
- Wait for navigation to complete (`page.wait_for_navigation()`)
- Capture `page.url` as final URL
- Capture full redirect chain from Playwright's network interception
- Close browser context (do not keep alive)

### 5.4 Data Written Per Extraction

```python
{
  "reel_url": "https://instagram.com/reel/...",
  "creator_username": "@username",
  "creator_user_id": 123456789,
  "dm_message_text": "Hey! Click here to get your reward",
  "dm_received_at": "2026-03-11T14:32:00Z",
  "original_url": "https://bit.ly/xxxxx",
  "redirect_chain": ["https://bit.ly/xxxxx", "https://landing.example.com/r?ref=ig"],
  "final_url": "https://landing.example.com/offer/premium",
  "extraction_method": "requests" | "playwright",
  "extracted_at": "2026-03-11T14:33:15Z",
  "session_id": "sess_abc123"
}
```

---

## **MODULE 6 — STORAGE LAYER**

### 6.1 PostgreSQL Schema

```sql
-- Accounts
CREATE TABLE accounts (
    id              SERIAL PRIMARY KEY,
    ig_username     VARCHAR(50) UNIQUE NOT NULL,
    session_file    VARCHAR(255),          -- path to encrypted vault file
    proxy_id        INTEGER REFERENCES proxies(id),
    health_score    INTEGER DEFAULT 100,
    status          VARCHAR(20) DEFAULT 'active',  -- active|quarantine|banned
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Proxies
CREATE TABLE proxies (
    id              SERIAL PRIMARY KEY,
    host            VARCHAR(100),
    port            INTEGER,
    username        VARCHAR(100),
    password        VARCHAR(100),
    country         VARCHAR(10),
    provider        VARCHAR(50),
    status          VARCHAR(20) DEFAULT 'active',  -- active|cooling|degraded|retired
    last_used_at    TIMESTAMPTZ
);

-- Reels
CREATE TABLE reels (
    id              SERIAL PRIMARY KEY,
    url             TEXT UNIQUE NOT NULL,
    media_id        VARCHAR(100),
    creator_username VARCHAR(50),
    creator_user_id  BIGINT,
    submitted_at    TIMESTAMPTZ DEFAULT NOW(),
    follow_status   VARCHAR(30),  -- skipped|followed|already_following|private_pending
    followed_at     TIMESTAMPTZ,
    comment_text    TEXT,
    comment_posted_at TIMESTAMPTZ,
    job_status      VARCHAR(30) DEFAULT 'pending'
    -- pending|follow_pending|commenting|completed|failed|challenge_pause
);

-- DM Messages
CREATE TABLE dm_messages (
    id              SERIAL PRIMARY KEY,
    reel_id         INTEGER REFERENCES reels(id),
    creator_user_id BIGINT,
    message_id      VARCHAR(100) UNIQUE,
    message_text    TEXT,
    cta_detected    BOOLEAN DEFAULT FALSE,
    cta_confidence  FLOAT,
    received_at     TIMESTAMPTZ
);

-- Extracted Links
CREATE TABLE extracted_links (
    id              SERIAL PRIMARY KEY,
    dm_message_id   INTEGER REFERENCES dm_messages(id),
    original_url    TEXT,
    redirect_chain  JSONB,
    final_url       TEXT,
    extraction_method VARCHAR(20),
    extracted_at    TIMESTAMPTZ DEFAULT NOW(),
    exported_to_excel BOOLEAN DEFAULT FALSE
);

-- Follows
CREATE TABLE follows (
    id              SERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES accounts(id),
    creator_user_id BIGINT,
    creator_username VARCHAR(50),
    followed_at     TIMESTAMPTZ,
    follow_back     BOOLEAN DEFAULT FALSE,
    follow_back_at  TIMESTAMPTZ
);

-- Task Audit Log
CREATE TABLE task_log (
    id              SERIAL PRIMARY KEY,
    task_id         VARCHAR(100),
    task_type       VARCHAR(50),
    account_id      INTEGER,
    reel_id         INTEGER,
    status          VARCHAR(20),
    error_message   TEXT,
    retries         INTEGER DEFAULT 0,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);
```

### 6.2 Redis Key Namespace

```
session:{account_id}:state          → current session metadata
ratelimit:{account_id}:follows      → sorted set (sliding window)
ratelimit:{account_id}:comments     → sorted set (sliding window)
seen_messages:{account_id}          → set of processed message IDs
watched_creators:{account_id}       → set of creator user IDs to watch
friendship_cache:{account_id}:{uid} → cached friendship status (1h TTL)
health:{account_id}                 → health score + last update
challenge:{account_id}:code         → operator-submitted resolution code
```

### 6.3 Excel Export Structure
- File: `exports/instaflow_export_{YYYY-MM-DD}.xlsx`
- One sheet: `Extracted Links`
- Columns: `#`, `Reel URL`, `Creator`, `DM Message`, `Original URL`, `Final URL`, `Redirect Hops`, `Method`, `Timestamp`
- Auto-column-width formatting
- New file created per day, rows appended throughout day
- Master cumulative file also maintained: `exports/instaflow_ALL_TIME.xlsx`

---

## **MODULE 7 — CONTROL API (FastAPI)**

### Endpoints

```
POST   /api/reels/submit          → Submit single or batch reel URLs
GET    /api/reels                 → List all reels with job status
GET    /api/reels/{id}            → Single reel detail

GET    /api/account/health        → Current health score + status
GET    /api/account/session       → Session validity check

GET    /api/links/extracted       → All extracted links (paginated)
GET    /api/links/export          → Download latest Excel file

POST   /api/control/pause         → Pause all workers
POST   /api/control/resume        → Resume workers
POST   /api/challenge/resolve     → Submit challenge code

GET    /api/stats/summary         → Dashboard stats (totals, rates, health)
GET    /ws/feed                   → WebSocket: live task event stream
```

---

## **MODULE 8 — OBSERVABILITY**

### 8.1 Structured Logging
- All logs emitted as JSON via `structlog`
- Every log entry includes: `timestamp`, `account_id`, `task_id`, `action`, `reel_id`, `duration_ms`, `result`
- Log levels: `DEBUG` (dev), `INFO` (prod default), `WARNING` (rate limit hits), `ERROR` (failures)
- Log output: rotating file (`logs/instaflow.log`, 10MB max, 5 rotations kept)

### 8.2 Alerting (Telegram Bot)
- Operator receives Telegram DM for:
  - Account health score drops below 40
  - Challenge/CAPTCHA triggered (with account name)
  - Account quarantined
  - Dead letter queue receives a task
  - Daily summary: comments posted, follows made, links extracted
- Simple bot: one bot token, one chat ID, stored in `.env`

### 8.3 Celery Flower (Optional)
- Flower dashboard for real-time Celery worker monitoring
- Exposed on port 5555
- Shows: active tasks, task history, worker status, queue depths

---

## **DIRECTORY STRUCTURE**

```
instaflow/
│
├── config/
│   ├── settings.py              # All env vars loaded via pydantic-settings
│   ├── comments.yaml            # Comment templates + synonym banks
│   └── cta_keywords.yaml        # CTA detection keyword list
│
├── core/
│   ├── session_vault.py         # Encrypted session load/save
│   ├── fingerprint.py           # Device profile management
│   ├── health_monitor.py        # Score tracking + quarantine logic
│   ├── challenge_handler.py     # Challenge hook + Telegram notify
│   └── proxy_manager.py         # Pool management + sticky assignment
│
├── stealth/
│   ├── timing.py                # Log-normal delay generator
│   ├── comment_engine.py        # Template variation + LLM fallback
│   └── rate_limiter.py          # Redis sliding window implementation
│
├── instagram/
│   ├── client.py                # instagrapi wrapper + session bootstrap
│   ├── follow.py                # follow status check + follow action
│   ├── comment.py               # comment posting
│   ├── dm_monitor.py            # DM polling + CTA detection
│   └── browser.py               # Playwright session for link extraction
│
├── workers/
│   ├── celery_app.py            # Celery app init + queue config
│   ├── task_reel.py             # process_reel task
│   ├── task_dm.py               # watch_dm task
│   ├── task_extract.py          # extract_link task
│   └── task_maintenance.py      # health_check + export_excel tasks
│
├── storage/
│   ├── database.py              # SQLAlchemy engine + session factory
│   ├── models.py                # All ORM models
│   ├── redis_client.py          # Redis connection + key helpers
│   └── excel_exporter.py        # openpyxl export logic
│
├── api/
│   ├── main.py                  # FastAPI app init
│   ├── routes/
│   │   ├── reels.py
│   │   ├── account.py
│   │   ├── links.py
│   │   ├── control.py
│   │   └── stats.py
│   └── websocket.py             # Live event feed
│
├── migrations/                  # Alembic DB migrations
├── exports/                     # Excel output files
├── logs/                        # Rotating log files
├── vaults/                      # Encrypted session files (gitignored)
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
└── README.md
```

---

## **TECH STACK — FINAL REFERENCE**

| Concern | Technology | Reason |
|---|---|---|
| Language | Python 3.11+ | Best Instagram library ecosystem |
| Instagram API | `instagrapi` | Stable private API client |
| Browser sim | `playwright` | Best-in-class, async, headless |
| Task queue | `Celery 5` + `Redis` | Battle-tested async worker system |
| Scheduler | `Celery Beat` | Native periodic task support |
| Database | `PostgreSQL` + `SQLAlchemy 2` | Relational, reliable, queryable |
| Migrations | `Alembic` | Schema version control |
| Cache / Broker | `Redis 7` | Fast, TTL-native, Celery-native |
| API server | `FastAPI` | Async, typed, auto-docs |
| Config | `pydantic-settings` | Type-safe env var loading |
| Encryption | `cryptography` (Fernet) | Session vault security |
| Excel export | `openpyxl` | Mature xlsx library |
| HTTP client | `httpx` | Async, redirect-aware |
| Logging | `structlog` | Structured JSON logs |
| Alerting | `python-telegram-bot` | Lightweight operator notifications |
| Containers | `Docker` + `Docker Compose` | Full local or VPS deployment |
| CAPTCHA | `2captcha` API | Automated challenge solving |
| LLM (optional) | `ollama` (local) | Contextual comment generation |

---

## **DEPLOYMENT — DOCKER COMPOSE SERVICES**

```yaml
services:
  postgres:     # Port 5432
  redis:        # Port 6379
  api:          # Port 8000 — FastAPI server
  worker:       # Celery worker (3 concurrency)
  beat:         # Celery Beat scheduler
  flower:       # Port 5555 — Worker monitor (optional)
  playwright:   # Playwright browser pool
```

Run on any Linux VPS (2 vCPU, 2GB RAM minimum). Recommended: Hetzner CX21 (~€4/mo) or DigitalOcean Basic Droplet (~$6/mo). Use a VPS in the **same country** as your Instagram account's registration country.

---

## **PHASED BUILD PLAN**

| Phase | Scope | Deliverable |
|---|---|---|
| 1 | Session vault, fingerprint, instagrapi client bootstrap | Login + session persist working |
| 2 | Follow status check + follow action + rate limiter | Safe follow flow end-to-end |
| 3 | Comment variation engine + comment posting | Full reel engagement loop |
| 4 | Celery workers + task orchestration + DB | Async batch processing |
| 5 | DM monitor + CTA detection + link resolver | Link extraction to PostgreSQL |
| 6 | Excel exporter + Telegram alerts | Operator-ready output |
| 7 | FastAPI control plane + WebSocket feed | Full dashboard API |
| 8 | Proxy manager + health monitor + challenge handler | Production hardening |
| 9 | Docker Compose + env config + README | Deployable, documented system |

---

Feed this document to your coding agent phase by phase. Start with Phase 1 and validate each phase is working end-to-end before moving to the next. The system is designed so each phase is independently testable.