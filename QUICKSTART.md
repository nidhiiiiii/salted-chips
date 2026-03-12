# InstaFlow Quick Start Checklist

Use this checklist to get InstaFlow running in under 10 minutes.

---

## **☐ Step 1: Copy Environment File**

```bash
cp .env.example .env
```

---

## **☐ Step 2: Edit .env with Your Credentials**

Required fields to change:

```bash
# Database password (create a strong one)
POSTGRES_PASSWORD=change_this_to_secure_password

# Instagram credentials
IG_USERNAME=your_instagram_username
IG_PASSWORD=your_instagram_password
IG_COUNTRY=US  # or IN, GB, etc. (your account's registration country)

# Encryption key (generate new one)
VAULT_ENCRYPTION_KEY=<run command below>
```

Generate encryption key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Optional but recommended:
```bash
# Telegram alerts
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=123456789
```

---

## **☐ Step 3: Start Docker Services**

```bash
docker-compose up -d
```

Wait 10 seconds for services to initialize, then verify:

```bash
docker-compose ps
```

All services should show `Up` status.

---

## **☐ Step 4: Run Database Migrations**

```bash
docker-compose exec worker alembic upgrade head
```

Expected output: `Running upgrade  -> 001_initial, Initial schema`

---

## **☐ Step 5: Add Your Instagram Account**

```bash
docker-compose exec postgres psql -U postgres -d instaflow
```

Then run:
```sql
INSERT INTO accounts (ig_username, country, status, health_score)
VALUES ('your_instagram_username', 'US', 'active', 100);
```

Exit: `\q`

---

## **☐ Step 6: Test with a Reel**

```bash
docker-compose exec worker python -m instaflow.cli submit \
  --url "https://www.instagram.com/reel/EXAMPLE123/" \
  --account-id 1
```

---

## **☐ Step 7: Monitor**

**View logs:**
```bash
docker-compose logs -f worker
```

**API Dashboard:**
```
http://localhost:8000/docs
```

**Celery Flower (task monitoring):**
```
http://localhost:5555
```

**Check stats:**
```bash
docker-compose exec worker python -m instaflow.cli stats
```

---

## **Troubleshooting**

| Problem | Solution |
|---------|----------|
| `connection refused` on port 5432 | PostgreSQL not running - `docker-compose restart postgres` |
| `Redis connection error` | Redis not running - `docker-compose restart redis` |
| Worker not processing tasks | Check worker logs - `docker-compose logs worker` |
| Instagram login fails | Check credentials in `.env`, may need manual challenge resolution |
| Migration fails | Rollback and retry - `alembic downgrade -1 && alembic upgrade head` |

---

## **Next Steps**

1. **Add proxy pool** (recommended for production)
2. **Configure Telegram alerts** for challenge notifications
3. **Submit batch reels** via `reels.txt` file
4. **Set up monitoring** alerts
5. **Deploy to VPS** for 24/7 operation

---

## **Commands Reference**

```bash
# Start all services
docker-compose up -d

# Stop all services
docker-compose down

# View logs
docker-compose logs -f

# Restart a service
docker-compose restart worker

# Run CLI commands
docker-compose exec worker python -m instaflow.cli --help

# Access database
docker-compose exec postgres psql -U postgres -d instaflow

# Run migrations
docker-compose exec worker alembic upgrade head

# Check migration status
docker-compose exec worker alembic current
```

---

## **Safety Reminders**

⚠️ **NEVER:**
- Commit `.env` file (it's gitignored for a reason)
- Use rate limits higher than recommended
- Run multiple accounts from same IP without proxies
- Ignore challenge alerts from Telegram

✅ **ALWAYS:**
- Start with conservative rate limits
- Monitor health scores daily
- Keep session vaults encrypted
- Use residential proxies in production
- Backup your database regularly

---

**Full documentation:** See `Readme.md` and `SETUP.md`
