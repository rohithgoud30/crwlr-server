import os
import logging

# Setup logging
logger = logging.getLogger(__name__)

def check_firebase_env_vars():
    """Validate Firebase environment variables at startup."""
    logger.info("Validating Firebase environment variables...")
    
    # Required Firebase variables
    required_vars = {
        "FIREBASE_TYPE": "Type of account (should be 'service_account')",
        "FIREBASE_PROJECT_ID": "Firebase project ID",
        "FIREBASE_PRIVATE_KEY_ID": "Firebase private key ID",
        "FIREBASE_PRIVATE_KEY": "Firebase private key",
        "FIREBASE_CLIENT_EMAIL": "Firebase client email",
    }
    
    # Optional Firebase variables
    optional_vars = {
        "FIREBASE_CLIENT_ID": "Firebase client ID",
        "FIREBASE_AUTH_URI": "Firebase auth URI",
        "FIREBASE_TOKEN_URI": "Firebase token URI",
        "FIREBASE_AUTH_PROVIDER_CERT_URL": "Firebase auth provider cert URL",
        "FIREBASE_CLIENT_CERT_URL": "Firebase client cert URL"
    }
    
    missing_required = []
    for var, description in required_vars.items():
        value = os.environ.get(var)
        if not value:
            logger.error(f"❌ MISSING REQUIRED: {var} - {description}")
            missing_required.append(var)
        else:
            # Mask sensitive values in logs
            if "PRIVATE_KEY" in var:
                display_value = f"{value[:15]}...{value[-15:]}" if len(value) > 30 else "[too short]"
                logger.info(f"✅ Found: {var} - {display_value} (length: {len(value)})")
            else:
                logger.info(f"✅ Found: {var} - {value}")
    
    for var, description in optional_vars.items():
        value = os.environ.get(var)
        if value:
            logger.info(f"✅ Found optional: {var}")
        else:
            logger.warning(f"⚠️ Missing optional: {var} - {description}")
    
    # Check format of private key
    pk = os.environ.get("FIREBASE_PRIVATE_KEY", "")
    if pk:
        # Check if the key appears to be properly formatted
        if not (pk.startswith("-----BEGIN PRIVATE KEY-----") or "-----BEGIN PRIVATE KEY-----" in pk):
            logger.error("❌ FIREBASE_PRIVATE_KEY doesn't have proper format - missing header")
            missing_required.append("FIREBASE_PRIVATE_KEY_FORMAT_ERROR")
            
        if not (pk.endswith("-----END PRIVATE KEY-----") or "-----END PRIVATE KEY-----" in pk):
            logger.error("❌ FIREBASE_PRIVATE_KEY doesn't have proper format - missing footer")
            missing_required.append("FIREBASE_PRIVATE_KEY_FORMAT_ERROR")
            
        # Check for escaped newlines that need to be converted
        if "\\n" in pk:
            logger.warning("⚠️ FIREBASE_PRIVATE_KEY contains escaped newlines (\\n) that must be converted")
    
    return len(missing_required) == 0

def check_database_env_vars():
    """Validate database environment variables at startup."""
    logger.info("Validating database environment variables... (optional)")
    
    # These variables are only required if using PostgreSQL database
    # Mark them as optional for now
    optional_vars = {
        "DB_USER": "Database username",
        "DB_PASS": "Database password",
        "DB_NAME": "Database name",
        "INSTANCE_CONNECTION_NAME": "Cloud SQL instance connection name"
    }
    
    missing_vars = []
    for var, description in optional_vars.items():
        value = os.environ.get(var)
        if not value:
            logger.warning(f"⚠️ Missing optional DB var: {var} - {description}")
            missing_vars.append(var)
        else:
            # Don't log sensitive values
            if var == "DB_PASS":
                logger.info(f"✅ Found: {var} - [MASKED]")
            else:
                logger.info(f"✅ Found: {var} - {value}")
    
    if missing_vars:
        logger.warning(f"Database variables missing: {', '.join(missing_vars)}")
        logger.warning("Database functionality will be limited, but Firebase can still work")
        
    # Always return True since database variables are optional
    return True

def check_api_env_vars():
    """Validate API environment variables at startup."""
    logger.info("Validating API environment variables...")
    
    # Required vars for API
    required_vars = {
        "API_KEY": "API authentication key",
        "PROJECT_ID": "Google Cloud project ID"
    }
    
    missing_required = []
    for var, description in required_vars.items():
        value = os.environ.get(var)
        if not value:
            logger.error(f"❌ MISSING REQUIRED: {var} - {description}")
            missing_required.append(var)
        else:
            # Mask sensitive values in logs
            if var == "API_KEY":
                display_value = f"{value[:6]}...{value[-6:]}" if len(value) > 12 else "[too short]"
                logger.info(f"✅ Found: {var} - {display_value}")
            else:
                logger.info(f"✅ Found: {var} - {value}")
    
    return len(missing_required) == 0

def validate_environment():
    """
    Validate all required environment variables are present and properly formatted.
    Returns True if all validations pass, False otherwise.
    """
    logger.info("Starting environment validation...")
    
    firebase_valid = check_firebase_env_vars()
    db_valid = check_database_env_vars()  # This will now always return True
    api_valid = check_api_env_vars()
    
    # Additional environment checks
    environment = os.environ.get("ENVIRONMENT")
    logger.info(f"Running in environment: {environment or 'not set'}")
    
    # Only Firebase and API checks are required
    if firebase_valid and api_valid:
        logger.info("✅ Environment validation successful")
        return True
    else:
        logger.error("❌ Environment validation failed - some required variables are missing")
        return False 