# InstaFlow Setup Guide

This guide walks you through setting up the complete InstaFlow automation platform from scratch.

---

## **Prerequisites**

- **Docker** and **Docker Compose** installed
- **Python 3.11+** (for local development)
- **PostgreSQL 15+** (or use Docker)
- **Redis 7+** (or use Docker)

---

## **Option 1: Quick Start with Docker (Recommended)**

This is the easiest way to get everything running.

### **Step 1: Clone and Setup Environment**

```bash
cd /path/to/agent

# Copy environment template
cp .env.example .env

# Edit .env with your credentials
nano .env  # or use your preferred editor
```

### **Step 2: Configure Environment Variables**

Edit `.env` with your settings:

```bash
# ── Database ─────────────────────────────────────────────────────────
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password_here
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=instaflow

DATABASE_URL=postgresql+asyncpg://postgres:your_secure_password_here@postgres:5432/instaflow

# ── Redis ────────────────────────────────────────────────────────────
REDIS_URL=redis://redis:6379/0

# ── Celery ───────────────────────────────────────────────────────────
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
CELERY_CONCURRENCY=3

# ── Instagram Account ────────────────────────────────────────────────
# Your Instagram credentials (NEVER commit this file!)
IG_USERNAME=your_account_username
IG_PASSWORD=your_account_password
IG_COUNTRY=IN  # Your account's registration country (e.g., US, IN, GB)

# ── Telegram Alerts (Optional but Recommended) ───────────────────────
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# ── Application ──────────────────────────────────────────────────────
DEBUG=false
LOG_LEVEL=INFO
```

### **Step 3: Start All Services**

```bash
# Build and start all containers
docker-compose up -d --build

# Check if all services are running
docker-compose ps
```

You should see:
```
NAME                STATUS              PORTS
postgres            Up                  5432/tcp
redis               Up                  6379/tcp
api                 Up                  0.0.0.0:8000->8000/tcp
worker              Up                  
beat                Up                  
flower              Up                  0.0.0.0:5555->5555/tcp
```

### **Step 4: Run Database Migrations**

```bash
# Run Alembic migrations to create database schema
docker-compose exec worker alembic upgrade head
```

You should see:
```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 001_initial, Initial schema
```

### **Step 5: Verify Setup**

```bash
# Check API is running
curl http://localhost:8000/api/stats/summary

# Check Flower dashboard (Celery monitoring)
open http://localhost:5555

# Check logs
docker-compose logs -f worker
```

### **Step 6: Add Your First Account**

You need to add an Instagram account to the database. Connect to PostgreSQL:

```bash
docker-compose exec postgres psql -U postgres -d instaflow
```

Then run:

```sql
INSERT INTO accounts (ig_username, country, status, health_score)
VALUES ('your_instagram_username', 'IN', 'active', 100);
```

Replace `'your_instagram_username'` and `'IN'` with your actual username and country code.

### **Step 7: Submit Your First Reel**

```bash
# Using the CLI
docker-compose exec worker python -m instaflow.cli submit \
  --url "https://www.instagram.com/reel/EXAMPLE123/" \
  --account-id 1

# Or via API
curl -X POST http://localhost:8000/api/reels/submit \
  -H "Content-Type: application/json" \
  -d '{"reel_urls": ["https://www.instagram.com/reel/EXAMPLE123/"], "account_id": 1}'
```

---

## **Option 2: Manual Setup (Local Development)**

Use this if you want to run services separately for development.

### **Step 1: Install PostgreSQL**

**macOS:**
```bash
brew install postgresql@15
brew services start postgresql@15
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get update
sudo apt-get install postgresql-15 postgresql-contrib
sudo systemctl start postgresql
```

**Create Database:**
```bash
sudo -u postgres psql

CREATE DATABASE instaflow;
CREATE USER postgres WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE instaflow TO postgres;
\q
```

### **Step 2: Install Redis**

**macOS:**
```bash
brew install redis
brew services start redis
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install redis-server
sudo systemctl start redis
```

**Verify Redis:**
```bash
redis-cli ping
# Should return: PONG
```

### **Step 3: Setup Python Environment**

```bash
cd /path/to/agent

# Create virtual environment (if not exists)
python -m venv .venv

# Activate it
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install
```

### **Step 4: Configure Environment**

Create `.env` file (copy from `.env.example`):

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# Database (local)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=instaflow

DATABASE_URL=postgresql+asyncpg://postgres:your_password@localhost:5432/instaflow

# Redis (local)
REDIS_URL=redis://localhost:6379/0

# Celery
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
CELERY_CONCURRENCY=3

# Instagram
IG_USERNAME=your_username
IG_PASSWORD=your_password
IG_COUNTRY=IN

# Telegram (optional)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

### **Step 5: Run Database Migrations**

```bash
export $(cat .env | xargs)  # Load environment variables
alembic upgrade head
```

### **Step 6: Start Services Manually**

You'll need separate terminal windows:

**Terminal 1 - API Server:**
```bash
source .venv/bin/activate
uvicorn instaflow.api.main:app --reload --host 0.0.0.0 --port 8000
```

**Terminal 2 - Celery Worker:**
```bash
source .venv/bin/activate
celery -A instaflow.workers.celery_app worker --loglevel=info --concurrency=3
```

**Terminal 3 - Celery Beat (Scheduler):**
```bash
source .venv/bin/activate
celery -A instaflow.workers.celery_app beat --loglevel=info
```

**Terminal 4 - Flower (Monitoring - Optional):**
```bash
source .venv/bin/activate
celery -A instaflow.workers.celery_app flower --port=5555
```

### **Step 7: Add Account and Test**

```bash
# Add account via psql
psql -U postgres -d instaflow

INSERT INTO accounts (ig_username, country, status, health_score)
VALUES ('your_username', 'IN', 'active', 100);

# Submit reel
python -m instaflow.cli submit --url "https://www.instagram.com/reel/EXAMPLE123/" --account-id 1
```

---

## **Infrastructure Components Explained**

| Service | Purpose | Port | Required? |
|---------|---------|------|-----------|
| **PostgreSQL** | Main database (accounts, reels, links, tasks) | 5432 | ✅ Yes |
| **Redis** | Message broker + cache + rate limiting | 6379 | ✅ Yes |
| **FastAPI** | REST API server | 8000 | ✅ Yes |
| **Celery Worker** | Background task processing | - | ✅ Yes |
| **Celery Beat** | Periodic task scheduler | - | ✅ Yes |
| **Flower** | Celery monitoring dashboard | 5555 | Optional |
| **Playwright** | Browser automation (for complex links) | - | Auto-started |

---

## **Production Deployment (VPS)**

For production, deploy on a VPS in the **same country** as your Instagram account.

### **Recommended VPS Specs**

- **Minimum:** 2 vCPU, 2GB RAM (Hetzner CX21, DigitalOcean Droplet)
- **Recommended:** 4 vCPU, 4GB RAM (for multiple accounts)

### **Setup Steps**

```bash
# 1. SSH into your VPS
ssh user@your-vps-ip

# 2. Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 3. Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# 4. Clone your repo
git clone <your-repo-url>
cd agent

# 5. Setup .env
cp .env.example .env
nano .env  # Edit with production credentials

# 6. Start services
docker-compose up -d

# 7. Run migrations
docker-compose exec worker alembic upgrade head

# 8. Add account
docker-compose exec postgres psql -U postgres -d instaflow -c \
  "INSERT INTO accounts (ig_username, country, status) VALUES ('your_user', 'US', 'active');"
```

### **Security Recommendations**

1. **Enable firewall:**
   ```bash
   sudo ufw allow 22/tcp    # SSH
   sudo ufw allow 8000/tcp  # API (or use nginx reverse proxy)
   sudo ufw enable
   ```

2. **Use environment-specific secrets** (never commit `.env`)

3. **Set up SSL** with nginx + Let's Encrypt for API endpoints

4. **Restrict database access** to localhost only

---

## **Troubleshooting**

### **PostgreSQL Connection Errors**

```bash
# Check if PostgreSQL is running
docker-compose ps postgres

# View logs
docker-compose logs postgres

# Restart
docker-compose restart postgres
```

### **Redis Connection Errors**

```bash
# Test Redis connection
docker-compose exec redis redis-cli ping

# Should return: PONG
```

### **Celery Worker Not Processing Tasks**

```bash
# Check worker logs
docker-compose logs worker

# Restart worker
docker-compose restart worker

# Check queue depth in Flower
open http://localhost:5555
```

### **Instagram Login Failures**

1. Check credentials in `.env`
2. Account may need manual challenge resolution
3. Check `logs/instaflow.log` for details
4. Use Telegram alerts for challenge notifications

### **Database Migration Errors**

```bash
# Check current migration version
docker-compose exec worker alembic current

# Rollback one migration
docker-compose exec worker alembic downgrade -1

# Re-run all
docker-compose exec worker alembic upgrade head
```

---

## **Next Steps After Setup**

1. **Configure Telegram Bot** (for alerts):
   - Message @BotFather on Telegram
   - Create new bot, get token
   - Get your chat ID via @userinfobot
   - Add to `.env`

2. **Add Proxy Pool** (recommended for stealth):
   ```sql
   INSERT INTO proxies (host, port, username, password, country, provider, status)
   VALUES ('proxy.example.com', 8080, 'user', 'pass', 'US', 'Smartproxy', 'active');
   ```

3. **Submit Batch Reels**:
   ```bash
   # Create reels.txt with one URL per line
   python -m instaflow.cli submit --file reels.txt --account-id 1
   ```

4. **Monitor Dashboard**:
   - API Docs: http://localhost:8000/docs
   - Flower: http://localhost:5555

5. **Export Results**:
   ```bash
   curl -O http://localhost:8000/api/links/export
   ```

---

## **Support**

For issues or questions:
- Check logs: `docker-compose logs -f`
- Review architecture: `Readme.md`
- Inspect database: `docker-compose exec postgres psql -U postgres -d instaflow`
