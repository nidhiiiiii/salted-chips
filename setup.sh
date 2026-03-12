#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# InstaFlow Quick Setup Script
# Automatically configures and starts all required services
# ──────────────────────────────────────────────────────────────────────────────

set -e

echo "🚀 InstaFlow Setup Script"
echo "========================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ── Step 1: Check .env file ──────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  No .env file found. Creating from template...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env file${NC}"
    echo ""
    echo -e "${YELLOW}📝 Please edit .env and set:${NC}"
    echo "   - POSTGRES_PASSWORD (your database password)"
    echo "   - IG_USERNAME (your Instagram username)"
    echo "   - IG_PASSWORD (your Instagram password)"
    echo "   - IG_COUNTRY (your account's country, e.g., US, IN, GB)"
    echo ""
    echo "Then run this script again."
    exit 1
fi

echo -e "${GREEN}✓ Found .env file${NC}"
echo ""

# ── Step 2: Stop existing containers ─────────────────────────────────────────
echo -e "${YELLOW}🛑 Stopping existing containers...${NC}"
docker stop instaflow_postgres instaflow_redis api worker beat flower 2>/dev/null || true
docker rm instaflow_postgres instaflow_redis api worker beat flower 2>/dev/null || true
echo -e "${GREEN}✓ Stopped old containers${NC}"
echo ""

# ── Step 3: Start PostgreSQL with correct credentials ────────────────────────
echo -e "${YELLOW}🐘 Starting PostgreSQL...${NC}"

# Read credentials from .env
POSTGRES_USER=$(grep "^POSTGRES_USER=" .env | cut -d'=' -f2)
POSTGRES_PASSWORD=$(grep "^POSTGRES_PASSWORD=" .env | cut -d'=' -f2)
POSTGRES_DB=$(grep "^POSTGRES_DB=" .env | cut -d'=' -f2)

docker run -d \
    --name instaflow_postgres \
    -e POSTGRES_USER="$POSTGRES_USER" \
    -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    -e POSTGRES_DB="$POSTGRES_DB" \
    -p 5432:5432 \
    -v postgres_data:/var/lib/postgresql/data \
    --restart unless-stopped \
    postgres:16-alpine

echo -e "${GREEN}✓ PostgreSQL started${NC}"
echo ""

# ── Step 4: Start Redis ──────────────────────────────────────────────────────
echo -e "${YELLOW}📦 Starting Redis...${NC}"

docker run -d \
    --name instaflow_redis \
    -p 6379:6379 \
    -v redis_data:/data \
    --restart unless-stopped \
    redis:7-alpine \
    redis-server --save 60 1 --loglevel warning

echo -e "${GREEN}✓ Redis started${NC}"
echo ""

# ── Step 5: Wait for services to be ready ────────────────────────────────────
echo -e "${YELLOW}⏳ Waiting for services to initialize...${NC}"
sleep 5

# Check PostgreSQL
echo -e "${YELLOW}🔍 Checking PostgreSQL health...${NC}"
for i in {1..10}; do
    if docker exec instaflow_postgres pg_isready -U "$POSTGRES_USER" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ PostgreSQL is ready${NC}"
        break
    fi
    if [ $i -eq 10 ]; then
        echo -e "${RED}✗ PostgreSQL failed to start. Check logs:${NC}"
        docker logs instaflow_postgres
        exit 1
    fi
    echo "   Waiting... ($i/10)"
    sleep 2
done

# Check Redis
echo -e "${YELLOW}🔍 Checking Redis health...${NC}"
if docker exec instaflow_redis redis-cli ping > /dev/null 2>&1; then
    echo -e "${GREEN}✓ Redis is ready${NC}"
else
    echo -e "${RED}✗ Redis failed to start${NC}"
    docker logs instaflow_redis
    exit 1
fi
echo ""

# ── Step 6: Run database migrations ──────────────────────────────────────────
echo -e "${YELLOW}🔧 Running database migrations...${NC}"

# Build the worker image first
docker-compose build worker

# Run migrations
docker-compose run --rm worker alembic upgrade head

echo -e "${GREEN}✓ Migrations completed${NC}"
echo ""

# ── Step 7: Add default account (optional) ───────────────────────────────────
echo -e "${YELLOW}👤 Would you like to add an Instagram account now? (y/n)${NC}"
read -r add_account

if [[ "$add_account" =~ ^[Yy]$ ]]; then
    echo -n "   Instagram username: "
    read -r ig_username
    
    echo -n "   Country code (e.g., US, IN, GB): "
    read -r ig_country
    
    docker exec -i instaflow_postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<EOF
INSERT INTO accounts (ig_username, country, status, health_score)
VALUES ('$ig_username', '$ig_country', 'active', 100);
EOF
    
    echo -e "${GREEN}✓ Account added${NC}"
fi
echo ""

# ── Step 8: Start all services ───────────────────────────────────────────────
echo -e "${YELLOW}🚀 Starting all services...${NC}"
docker-compose up -d

echo ""
echo -e "${GREEN}✅ Setup Complete!${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📊 Service Status:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
docker-compose ps
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📝 Next Steps:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "1. View logs:"
echo "   docker-compose logs -f worker"
echo ""
echo "2. Submit your first reel:"
echo "   docker-compose exec worker python -m instaflow.cli submit \\"
echo "     --url \"https://www.instagram.com/reel/EXAMPLE/\" \\"
echo "     --account-id 1"
echo ""
echo "3. Check stats:"
echo "   docker-compose exec worker python -m instaflow.cli stats"
echo ""
echo "4. API Dashboard: http://localhost:8000/docs"
echo "5. Flower Monitor: http://localhost:5555"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
