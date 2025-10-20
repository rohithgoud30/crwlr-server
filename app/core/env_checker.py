import os
import logging

from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)


def check_neon_env_vars() -> bool:
    """Validate Neon/PostgreSQL environment variables at startup."""
    logger.info("Validating Neon/PostgreSQL environment variables...")
    neon_url = settings.NEON_DATABASE_URL or os.environ.get("NEON_DATABASE_URL")
    if neon_url:
        masked = neon_url[:8] + "..." if len(neon_url) > 11 else neon_url
        logger.info(f"✅ NEON_DATABASE_URL set ({masked})")
        return True

    logger.error("❌ MISSING REQUIRED: NEON_DATABASE_URL - Neon/PostgreSQL connection string")
    return False


def check_api_env_vars() -> bool:
    """Validate API environment variables at startup."""
    logger.info("Validating API environment variables...")

    required_vars = {"API_KEY": "API authentication key"}
    missing_required = []

    for var, description in required_vars.items():
        value = getattr(settings, var, None) or os.environ.get(var)
        if not value:
            logger.error(f"❌ MISSING REQUIRED: {var} - {description}")
            missing_required.append(var)
        else:
            display_value = f"{value[:6]}...{value[-6:]}" if len(value) > 12 else "[too short]"
            logger.info(f"✅ Found: {var} - {display_value}")

    return len(missing_required) == 0


def validate_environment() -> bool:
    """Validate required environment variables."""
    logger.info("Starting environment validation...")

    neon_valid = check_neon_env_vars()
    api_valid = check_api_env_vars()

    environment = settings.ENVIRONMENT or os.environ.get("ENVIRONMENT")
    logger.info(f"Running in environment: {environment or 'not set'}")

    if neon_valid and api_valid:
        logger.info("✅ Environment validation successful")
        return True

    logger.error("❌ Environment validation failed - required variables are missing")
    return False
