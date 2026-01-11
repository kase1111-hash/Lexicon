# Troubleshooting Guide

This guide covers common issues and their solutions.

## Table of Contents

- [Installation Issues](#installation-issues)
- [Docker Issues](#docker-issues)
- [Database Issues](#database-issues)
- [API Issues](#api-issues)
- [Performance Issues](#performance-issues)
- [Testing Issues](#testing-issues)

---

## Installation Issues

### Python version error

**Error:**
```
ERROR: This package requires Python 3.11+
```

**Solution:**
```bash
# Check your Python version
python --version

# Install Python 3.11+ using pyenv
pyenv install 3.11.0
pyenv local 3.11.0

# Or use specific python command
python3.11 -m venv venv
```

### Dependency installation fails

**Error:**
```
ERROR: Could not build wheels for [package]
```

**Solution:**
```bash
# Install build dependencies (Ubuntu/Debian)
sudo apt-get install build-essential python3-dev

# Install build dependencies (macOS)
xcode-select --install

# Install build dependencies (Windows)
# Install Visual Studio Build Tools

# Retry installation
pip install -r requirements.txt
```

### Package conflicts

**Error:**
```
ERROR: Cannot install package-a and package-b because these package versions have conflicting dependencies
```

**Solution:**
```bash
# Create fresh virtual environment
rm -rf venv
python -m venv venv
source venv/bin/activate

# Install with fresh dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Docker Issues

### Docker daemon not running

**Error:**
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock
```

**Solution:**
```bash
# Linux
sudo systemctl start docker
sudo systemctl enable docker

# macOS/Windows
# Start Docker Desktop application

# Verify
docker info
```

### Container fails to start

**Error:**
```
ERROR: Container exited with code 1
```

**Solution:**
```bash
# Check logs
docker compose logs [service-name]

# Common fixes:
# 1. Check port conflicts
sudo lsof -i :7474  # Neo4j
sudo lsof -i :5432  # PostgreSQL
sudo lsof -i :9200  # Elasticsearch

# 2. Check memory
docker system info | grep Memory

# 3. Remove and recreate containers
docker compose down -v
docker compose up -d
```

### Out of memory

**Error:**
```
Container killed due to OOM
```

**Solution:**
```bash
# Increase Docker memory (Docker Desktop)
# Settings → Resources → Memory → 8GB+

# Or reduce service memory in docker-compose.yml
# environment:
#   - NEO4J_dbms_memory_heap_max__size=1G
```

### Port already in use

**Error:**
```
Error starting userland proxy: listen tcp 0.0.0.0:8000: bind: address already in use
```

**Solution:**
```bash
# Find process using port
sudo lsof -i :8000
# or
sudo netstat -tulpn | grep 8000

# Kill the process
kill -9 <PID>

# Or change port in docker-compose.yml
ports:
  - "8001:8000"
```

---

## Database Issues

### Neo4j connection refused

**Error:**
```
ServiceUnavailable: Unable to connect to bolt://localhost:7687
```

**Solution:**
```bash
# Check if Neo4j is running
docker compose ps neo4j

# Check logs
docker compose logs neo4j

# Verify connection
curl http://localhost:7474

# Common fixes:
# 1. Wait for startup (can take 30-60 seconds)
# 2. Check authentication
docker compose exec neo4j cypher-shell -u neo4j -p password "RETURN 1"
```

### PostgreSQL authentication failed

**Error:**
```
FATAL: password authentication failed for user "ls_user"
```

**Solution:**
```bash
# Check environment variables
cat .env | grep POSTGRES

# Verify credentials match docker-compose.yml
# Reset password
docker compose exec postgres psql -U postgres -c "ALTER USER ls_user PASSWORD 'new_password';"

# Update .env
POSTGRES_PASSWORD=new_password
```

### Elasticsearch cluster health yellow/red

**Error:**
```
Cluster health status: YELLOW
```

**Solution:**
```bash
# Check cluster health
curl http://localhost:9200/_cluster/health?pretty

# Yellow = replicas not assigned (normal for single-node)
# Red = primary shards missing

# For single-node development, set replicas to 0
curl -X PUT "localhost:9200/_settings" -H 'Content-Type: application/json' -d'
{
  "index.number_of_replicas": 0
}'
```

### Redis connection error

**Error:**
```
ConnectionError: Error connecting to Redis
```

**Solution:**
```bash
# Check if Redis is running
docker compose ps redis

# Test connection
docker compose exec redis redis-cli ping
# Should return: PONG

# Check Redis logs
docker compose logs redis
```

---

## API Issues

### API returns 500 Internal Server Error

**Solution:**
```bash
# Check API logs
docker compose logs api

# Or if running locally
tail -f logs/app.log

# Common causes:
# 1. Database not connected
# 2. Missing environment variables
# 3. Code errors - check stack trace in logs
```

### API returns 401 Unauthorized

**Error:**
```json
{"error": {"code": "AUTHENTICATION_ERROR", "message": "Invalid or missing API key"}}
```

**Solution:**
```bash
# Include API key in request
curl -H "X-API-Key: your-api-key" http://localhost:8000/lsr/search

# Or disable authentication in .env
API_KEY=
```

### API returns 429 Too Many Requests

**Error:**
```json
{"error": {"code": "RATE_LIMIT_ERROR", "message": "Rate limit exceeded"}}
```

**Solution:**
```bash
# Wait for rate limit window to reset
# Check X-RateLimit-Reset header for reset time

# Or increase limits in .env
RATE_LIMIT_REQUESTS=500
RATE_LIMIT_WINDOW_SECONDS=60
```

### CORS errors in browser

**Error:**
```
Access to fetch at 'http://localhost:8000' from origin 'http://localhost:3000' has been blocked by CORS policy
```

**Solution:**
```bash
# Add your origin to allowed origins in .env
CORS_ORIGINS=http://localhost:3000,http://localhost:8080

# For development, allow all
CORS_ORIGINS=*
```

### GraphQL query errors

**Error:**
```json
{"errors": [{"message": "Cannot query field 'xyz' on type 'LSR'"}]}
```

**Solution:**
```bash
# Check available fields in schema
curl http://localhost:8000/graphql -H "Content-Type: application/json" \
  -d '{"query": "{ __schema { types { name fields { name } } } }"}'

# Use introspection at /graphql (Swagger UI)
```

---

## Performance Issues

### Slow API responses

**Symptoms:** Requests take >1 second

**Solutions:**

1. **Enable caching**
   ```bash
   # Verify Redis is running
   docker compose ps redis
   ```

2. **Add database indexes**
   ```bash
   # Run index creation
   make db-init
   ```

3. **Optimize queries**
   ```bash
   # Use pagination
   curl "http://localhost:8000/lsr/search?limit=50&offset=0"

   # Use GraphQL to fetch only needed fields
   ```

4. **Check slow query log**
   ```bash
   # Neo4j
   docker compose exec neo4j cat /var/lib/neo4j/logs/query.log
   ```

### High memory usage

**Solution:**
```bash
# Check memory per container
docker stats

# Reduce heap sizes in docker-compose.yml
# Neo4j: NEO4J_dbms_memory_heap_max__size=1G
# Elasticsearch: ES_JAVA_OPTS=-Xms512m -Xmx512m

# Restart containers
docker compose restart
```

### Disk space issues

**Solution:**
```bash
# Check disk usage
df -h

# Clean Docker resources
docker system prune -a

# Clean build artifacts
make clean

# Reduce database retention
# Configure in respective database settings
```

---

## Testing Issues

### Tests fail with import errors

**Error:**
```
ModuleNotFoundError: No module named 'src'
```

**Solution:**
```bash
# Install package in development mode
pip install -e .

# Or set PYTHONPATH
export PYTHONPATH=/path/to/project

# Run tests with pytest
PYTHONPATH=. pytest tests/
```

### Tests fail with database errors

**Error:**
```
ConnectionError: Cannot connect to database
```

**Solution:**
```bash
# Start test databases
docker compose up -d neo4j postgres redis

# Or use test configuration
cp config/.env.development .env
```

### Tests timeout

**Solution:**
```bash
# Increase timeout
pytest tests/ --timeout=60

# Run specific tests
pytest tests/unit/ -v

# Skip slow tests
pytest tests/ -m "not slow"
```

---

## Getting More Help

### Enable debug logging

```bash
# In .env
LOG_LEVEL=DEBUG
LOG_FORMAT=text

# Restart services
docker compose restart
```

### Collect diagnostic information

```bash
# System info
make version-check
docker compose ps
docker system info

# Recent logs
docker compose logs --tail=100

# Database status
curl http://localhost:8000/health
```

### Report an issue

When reporting issues, include:
1. Error message and stack trace
2. Steps to reproduce
3. Environment (OS, Python version, Docker version)
4. Output of `make version-check`
5. Relevant log excerpts

Submit issues at: https://github.com/linguistic-stratigraphy/linguistic-stratigraphy/issues
