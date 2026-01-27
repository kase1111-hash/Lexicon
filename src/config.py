"""Secure configuration management for Linguistic Stratigraphy.

Provides:
- Type-safe configuration with Pydantic Settings
- Environment variable loading with .env file support
- Secrets manager integration (AWS Secrets Manager, HashiCorp Vault)
- Configuration validation on startup
- Sensitive value masking in logs
"""

import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SecretsManagerConfig(BaseSettings):
    """Configuration for external secrets managers."""

    model_config = SettingsConfigDict(env_prefix="SECRETS_")

    # Secrets manager type: none, aws, vault, gcp
    provider: Literal["none", "aws", "vault", "gcp"] = "none"

    # AWS Secrets Manager
    aws_region: str = "us-east-1"
    aws_secret_name: str = ""

    # HashiCorp Vault
    vault_url: str = ""
    vault_token: SecretStr | None = None
    vault_path: str = "secret/data/lexicon"

    # GCP Secret Manager
    gcp_project: str = ""


class DatabaseConfig(BaseSettings):
    """Database connection configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = Field(default=SecretStr(""))

    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "linguistic_stratigraphy"
    postgres_user: str = "ls_user"
    postgres_password: SecretStr = Field(default=SecretStr(""))

    # Elasticsearch
    elasticsearch_hosts: str = "http://localhost:9200"
    elasticsearch_api_key: SecretStr | None = None
    elasticsearch_cloud_id: str | None = None

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr | None = None
    redis_db: int = 0

    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530

    @property
    def postgres_dsn(self) -> str:
        """Get PostgreSQL connection string."""
        password = self.postgres_password.get_secret_value() if self.postgres_password else ""
        return f"postgresql://{self.postgres_user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"

    @property
    def redis_url(self) -> str:
        """Get Redis connection URL."""
        if self.redis_password:
            password = self.redis_password.get_secret_value()
            return f"redis://:{password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


class APIConfig(BaseSettings):
    """API server configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_workers: int = 4

    # Authentication
    api_key: SecretStr | None = None
    api_key_header: str = "X-API-Key"
    jwt_secret: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # CORS
    cors_origins: str = "*"
    cors_allow_credentials: bool = True

    # Rate limiting (enabled by default for security)
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    @property
    def cors_origins_list(self) -> list[str]:
        """Get CORS origins as a list."""
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",")]

    @field_validator("api_port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return v


class LoggingConfig(BaseSettings):
    """Logging configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_file: str | None = None

    # Component-specific levels
    api_log_level: str = "INFO"
    pipeline_log_level: str = "INFO"
    db_log_level: str = "WARNING"

    # Performance
    slow_request_threshold_ms: float = 1000.0

    @field_validator("log_level", "api_log_level", "pipeline_log_level", "db_log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid_levels:
            raise ValueError(f"Log level must be one of {valid_levels}")
        return v.upper()


class ErrorTrackingConfig(BaseSettings):
    """Error tracking and monitoring configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    # Sentry
    sentry_dsn: SecretStr | None = None
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.1

    # Environment
    environment: Literal["development", "staging", "production"] = "development"
    app_version: str = "0.1.0"
    debug: bool = False

    @field_validator("sentry_traces_sample_rate", "sentry_profiles_sample_rate")
    @classmethod
    def validate_sample_rate(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("Sample rate must be between 0.0 and 1.0")
        return v


class ExternalServicesConfig(BaseSettings):
    """External services configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    # Wiktionary
    wiktionary_rate_limit_ms: int = 100

    # Embedding model
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    # Airflow
    airflow_fernet_key: SecretStr | None = None
    airflow_secret_key: SecretStr | None = None


class Settings(BaseSettings):
    """Main application settings combining all configuration sections."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # Sub-configurations
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    error_tracking: ErrorTrackingConfig = Field(default_factory=ErrorTrackingConfig)
    external_services: ExternalServicesConfig = Field(default_factory=ExternalServicesConfig)
    secrets_manager: SecretsManagerConfig = Field(default_factory=SecretsManagerConfig)

    @model_validator(mode="after")
    def load_secrets_from_manager(self) -> "Settings":
        """Load secrets from external secrets manager if configured."""
        if self.secrets_manager.provider != "none":
            secrets = _load_from_secrets_manager(self.secrets_manager)
            if secrets:
                self._apply_secrets(secrets)
        return self

    def _apply_secrets(self, secrets: dict[str, Any]) -> None:
        """Apply secrets from external manager to configuration."""
        secret_mappings = {
            "neo4j_password": ("database", "neo4j_password"),
            "postgres_password": ("database", "postgres_password"),
            "redis_password": ("database", "redis_password"),
            "elasticsearch_api_key": ("database", "elasticsearch_api_key"),
            "api_key": ("api", "api_key"),
            "jwt_secret": ("api", "jwt_secret"),
            "sentry_dsn": ("error_tracking", "sentry_dsn"),
            "airflow_fernet_key": ("external_services", "airflow_fernet_key"),
            "airflow_secret_key": ("external_services", "airflow_secret_key"),
        }

        for secret_key, (section, attr) in secret_mappings.items():
            if secrets.get(secret_key):
                section_obj = getattr(self, section)
                setattr(section_obj, attr, SecretStr(secrets[secret_key]))

    def validate_required_for_production(self) -> list[str]:
        """Validate that required settings are configured for production."""
        errors = []

        if self.error_tracking.environment == "production":
            # Check required production settings
            if not self.database.neo4j_password.get_secret_value():
                errors.append("NEO4J_PASSWORD is required in production")
            if not self.database.postgres_password.get_secret_value():
                errors.append("POSTGRES_PASSWORD is required in production")
            if self.error_tracking.debug:
                errors.append("DEBUG must be False in production")
            if self.api.cors_origins == "*":
                errors.append("CORS_ORIGINS should not be '*' in production")
            if not self.api.rate_limit_enabled:
                errors.append("RATE_LIMIT_ENABLED should be True in production")

        return errors

    def mask_sensitive(self) -> dict[str, Any]:
        """Return configuration dict with sensitive values masked."""
        config = self.model_dump()
        return _mask_dict(config)


def _mask_dict(d: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Recursively mask sensitive values in a dictionary."""
    if depth > 10:  # Prevent infinite recursion
        return d

    sensitive_patterns = [
        r"password",
        r"secret",
        r"key",
        r"token",
        r"dsn",
        r"api_key",
        r"credential",
    ]
    pattern = re.compile("|".join(sensitive_patterns), re.IGNORECASE)

    result = {}
    for key, value in d.items():
        if isinstance(value, dict):
            result[key] = _mask_dict(value, depth + 1)
        elif pattern.search(key) and value:
            result[key] = "***MASKED***"
        else:
            result[key] = value
    return result


def _load_from_secrets_manager(config: SecretsManagerConfig) -> dict[str, Any]:
    """Load secrets from the configured secrets manager."""
    if config.provider == "aws":
        return _load_aws_secrets(config)
    elif config.provider == "vault":
        return _load_vault_secrets(config)
    elif config.provider == "gcp":
        return _load_gcp_secrets(config)
    return {}


def _load_aws_secrets(config: SecretsManagerConfig) -> dict[str, Any]:
    """Load secrets from AWS Secrets Manager."""
    if not config.aws_secret_name:
        return {}

    try:
        import json

        import boto3

        client = boto3.client("secretsmanager", region_name=config.aws_region)
        response = client.get_secret_value(SecretId=config.aws_secret_name)

        if "SecretString" in response:
            return json.loads(response["SecretString"])
    except ImportError:
        logger.debug("boto3 not installed, skipping AWS Secrets Manager")
    except Exception as e:
        logger.warning(f"Failed to load secrets from AWS Secrets Manager: {e}")

    return {}


def _load_vault_secrets(config: SecretsManagerConfig) -> dict[str, Any]:
    """Load secrets from HashiCorp Vault."""
    if not config.vault_url or not config.vault_token:
        return {}

    try:
        import hvac

        client = hvac.Client(
            url=config.vault_url,
            token=config.vault_token.get_secret_value() if config.vault_token else None,
        )

        if client.is_authenticated():
            secret = client.secrets.kv.v2.read_secret_version(path=config.vault_path)
            return secret.get("data", {}).get("data", {})
    except ImportError:
        logger.debug("hvac not installed, skipping HashiCorp Vault")
    except Exception as e:
        logger.warning(f"Failed to load secrets from HashiCorp Vault: {e}")

    return {}


def _load_gcp_secrets(config: SecretsManagerConfig) -> dict[str, Any]:
    """Load secrets from GCP Secret Manager."""
    if not config.gcp_project:
        return {}

    try:
        import json

        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()

        # List all secrets and load them
        secrets = {}
        parent = f"projects/{config.gcp_project}"

        for secret in client.list_secrets(request={"parent": parent}):
            secret_name = secret.name.split("/")[-1]
            version_name = f"{secret.name}/versions/latest"

            try:
                response = client.access_secret_version(request={"name": version_name})
                value = response.payload.data.decode("UTF-8")

                # Try to parse as JSON
                try:
                    secrets[secret_name] = json.loads(value)
                except json.JSONDecodeError:
                    secrets[secret_name] = value
            except Exception:
                continue

        return secrets
    except ImportError:
        logger.debug("google-cloud-secret-manager not installed, skipping GCP Secret Manager")
    except Exception as e:
        logger.warning(f"Failed to load secrets from GCP Secret Manager: {e}")

    return {}


@lru_cache
def get_settings() -> Settings:
    """
    Get the application settings singleton.

    Settings are cached and only loaded once. Uses LRU cache for thread safety.

    Returns:
        Settings instance with all configuration loaded.
    """
    # Check for custom env file path
    env_file = os.getenv("ENV_FILE", ".env")
    env_path = Path(env_file)

    if env_path.exists():
        return Settings(_env_file=env_path)
    return Settings()


def reload_settings() -> Settings:
    """
    Reload settings by clearing the cache.

    Returns:
        Fresh Settings instance.
    """
    get_settings.cache_clear()
    return get_settings()


# Convenience accessors
def get_database_config() -> DatabaseConfig:
    """Get database configuration."""
    return get_settings().database


def get_api_config() -> APIConfig:
    """Get API configuration."""
    return get_settings().api


def get_logging_config() -> LoggingConfig:
    """Get logging configuration."""
    return get_settings().logging


def get_error_tracking_config() -> ErrorTrackingConfig:
    """Get error tracking configuration."""
    return get_settings().error_tracking


def is_production() -> bool:
    """Check if running in production environment."""
    return get_settings().error_tracking.environment == "production"


def is_debug() -> bool:
    """Check if debug mode is enabled."""
    return get_settings().error_tracking.debug
