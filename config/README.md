# Environment Configuration

This directory contains environment-specific configuration files for different deployment stages.

## Files

| File | Purpose |
|------|---------|
| `.env.development` | Local development with Docker Compose |
| `.env.staging` | Staging environment for testing |
| `.env.production` | Production template (secrets via manager) |

## Usage

### Option 1: Copy to project root

```bash
# For development
cp config/.env.development .env

# For staging
cp config/.env.staging .env

# For production
cp config/.env.production .env
```

### Option 2: Use ENV_FILE environment variable

```bash
# Point to specific config file
export ENV_FILE=config/.env.development
python -m src.api.main
```

### Option 3: Docker Compose

```yaml
services:
  api:
    env_file:
      - config/.env.${ENVIRONMENT:-development}
```

## Environment Hierarchy

1. **Environment variables** (highest priority)
2. **`.env` file** in project root
3. **`ENV_FILE` path** if specified
4. **Default values** in code

## Secrets Management

### Development
- Secrets are stored directly in `.env.development`
- Uses local Docker services

### Staging/Production
- Set `SECRETS_PROVIDER` to use external secrets manager
- Supported providers:
  - `aws` - AWS Secrets Manager
  - `vault` - HashiCorp Vault
  - `gcp` - GCP Secret Manager

### AWS Secrets Manager Example

```bash
# Store secrets in AWS
aws secretsmanager create-secret \
  --name linguistic-stratigraphy/production \
  --secret-string '{
    "neo4j_password": "...",
    "postgres_password": "...",
    "jwt_secret": "...",
    "sentry_dsn": "..."
  }'
```

### HashiCorp Vault Example

```bash
# Store secrets in Vault
vault kv put secret/linguistic-stratigraphy/production \
  neo4j_password="..." \
  postgres_password="..." \
  jwt_secret="..." \
  sentry_dsn="..."
```

## Required Secrets by Environment

| Secret | Development | Staging | Production |
|--------|-------------|---------|------------|
| `NEO4J_PASSWORD` | ✓ (in file) | ✓ (secrets mgr) | ✓ (secrets mgr) |
| `POSTGRES_PASSWORD` | ✓ (in file) | ✓ (secrets mgr) | ✓ (secrets mgr) |
| `JWT_SECRET` | Optional | ✓ (secrets mgr) | ✓ (secrets mgr) |
| `API_KEY` | Optional | Optional | Recommended |
| `SENTRY_DSN` | Optional | ✓ (secrets mgr) | ✓ (secrets mgr) |

## Validation

Run production validation check:

```python
from src.config import get_settings

settings = get_settings()
errors = settings.validate_required_for_production()

if errors:
    for error in errors:
        print(f"ERROR: {error}")
```

## Security Notes

1. **Never commit secrets** - Use `.gitignore` to exclude `.env` files
2. **Use secrets managers** in staging/production
3. **Rotate secrets regularly** - Especially JWT secrets
4. **Limit CORS origins** - Never use `*` in production
5. **Enable rate limiting** - Protect against abuse
