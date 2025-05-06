import os
import logging
import json
import traceback
import firebase_admin
from firebase_admin import credentials, firestore
from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)

# Initialize db object for use by other modules
db = None

# Initialize Firebase
def initialize_firebase(force_init=False):
    """
    Initialize Firebase Admin SDK with proper admin credentials
    
    Args:
        force_init: If True, will attempt to initialize even with missing vars
    """
    global db  # Access the global db variable
    
    try:
        # Check if already initialized
        if firebase_admin._apps:
            logger.info("Firebase already initialized")
            if db is None:
                try:
                    db = firestore.client()
                    logger.info("Firestore client created successfully from existing app")
                except Exception as e:
                    handle_firebase_error(e)
                    logger.error(f"Could not create Firestore client from existing app: {str(e)}")
                    logger.error(traceback.format_exc())
            return db
        
        # Log environment for debugging
        logger.info(f"ENV check - ENVIRONMENT: {settings.ENVIRONMENT}")
        logger.info(f"ENV check - PROJECT_ID: {os.environ.get('PROJECT_ID', 'not set')}")
        
        # OVERRIDE: Force the correct project ID regardless of environment variables
        CORRECT_PROJECT_ID = "crwlr-server-ccfd2"
        logger.info(f"Using project ID: {CORRECT_PROJECT_ID} (hardcoded for testing)")
        
        # Get the Firebase project ID (use the override)
        firebase_project_id = CORRECT_PROJECT_ID  # Override with the correct ID
        
        # MODIFIED: First check for a credentials file since it's the most reliable method
        # If a service account file exists, try to use that first
        if os.path.exists("firebase-credentials.json"):
            logger.info("Initializing Firebase with admin service account file (firebase-credentials.json)")
            try:
                cred = credentials.Certificate("firebase-credentials.json")
                firebase_admin.initialize_app(cred)
                db = firestore.client()
                logger.info("Firebase initialized successfully with admin service account JSON file")
                
                # Verify connection with a simple test
                try:
                    # Test if we can access a collection
                    collection_ref = db.collection('documents')
                    logger.info(f"Firestore connection verified - successfully accessed 'documents' collection: {collection_ref.id}")
                    return db
                except Exception as test_err:
                    logger.warning(f"Initialized Firebase, but error when testing collection access: {str(test_err)}")
                    # Continue with app but log the warning
                    return db
                
            except Exception as e:
                logger.error(f"Error with credentials file: {str(e)}")
                logger.error(traceback.format_exc())
                handle_firebase_error(e)
                # Don't return here - let's try other methods
        else:
            logger.warning("firebase-credentials.json file not found")
        
        # Check for explicit credentials file path
        if os.path.exists("firebase-admin-credentials.json"):
            logger.info("Initializing Firebase with explicit admin credentials file (firebase-admin-credentials.json)")
            try:
                cred = credentials.Certificate("firebase-admin-credentials.json")
                firebase_admin.initialize_app(cred)
                db = firestore.client()
                logger.info("Firebase initialized successfully with explicit admin credentials file")
                return db
            except Exception as e:
                logger.error(f"Error with explicit admin credentials file: {str(e)}")
                logger.error(traceback.format_exc())
                handle_firebase_error(e)
                # Don't return here - let's try environment variables
        else:
            logger.warning("firebase-admin-credentials.json file not found")
            
        # Required minimum environment variables
        required_vars = ["FIREBASE_PRIVATE_KEY", "FIREBASE_CLIENT_EMAIL"]
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        
        # Check for missing required variables but only log warning
        if missing_vars:
            missing_list = ", ".join(missing_vars)
            logger.warning(f"Missing environment variables for Firebase: {missing_list}")
            if not force_init:
                logger.warning("Not forcing init - skipping environment variable method")
            
        # Verify all Firebase environment variables are available
        firebase_env_vars = {
            "FIREBASE_TYPE": os.environ.get("FIREBASE_TYPE"),
            "FIREBASE_PROJECT_ID": os.environ.get("FIREBASE_PROJECT_ID"),
            "FIREBASE_PRIVATE_KEY_ID": os.environ.get("FIREBASE_PRIVATE_KEY_ID"),
            "FIREBASE_CLIENT_EMAIL": os.environ.get("FIREBASE_CLIENT_EMAIL"),
            "FIREBASE_CLIENT_ID": os.environ.get("FIREBASE_CLIENT_ID"),
            "FIREBASE_AUTH_URI": os.environ.get("FIREBASE_AUTH_URI"),
            "FIREBASE_TOKEN_URI": os.environ.get("FIREBASE_TOKEN_URI"),
            "FIREBASE_AUTH_PROVIDER_CERT_URL": os.environ.get("FIREBASE_AUTH_PROVIDER_CERT_URL"),
            "FIREBASE_CLIENT_CERT_URL": os.environ.get("FIREBASE_CLIENT_CERT_URL")
        }
        
        # Log presence of environment variables (not their values)
        for key, value in firebase_env_vars.items():
            logger.info(f"ENV check - {key}: {'SET' if value else 'MISSING'}")
        
        # Get private key with enhanced error handling
        firebase_private_key = os.environ.get("FIREBASE_PRIVATE_KEY", "")
        if not firebase_private_key:
            logger.error("CRITICAL: FIREBASE_PRIVATE_KEY environment variable is empty or not set")
        
        # Fix: Handle all possible ways newlines might be escaped
        if "\\\\n" in firebase_private_key:  # Double escaped newlines
            logger.info("Found double-escaped newlines (\\\\n), converting them")
            firebase_private_key = firebase_private_key.replace("\\\\n", "\n")
        elif "\\n" in firebase_private_key:  # Single escaped newlines
            logger.info("Found escaped newlines (\\n), converting them")
            firebase_private_key = firebase_private_key.replace("\\n", "\n")
            
        # Check for quotes that might be included in the environment variable
        if firebase_private_key.startswith('"') and firebase_private_key.endswith('"'):
            logger.info("Found quotes wrapping the private key, removing them")
            firebase_private_key = firebase_private_key[1:-1]
            
        # Get client email
        firebase_client_email = os.environ.get("FIREBASE_CLIENT_EMAIL")
        if not firebase_client_email:
            logger.error("CRITICAL: FIREBASE_CLIENT_EMAIL environment variable is empty or not set")
        
        # Log key format for debugging (without exposing the actual key)
        if firebase_private_key:
            key_start = firebase_private_key[:25] if len(firebase_private_key) > 25 else firebase_private_key
            key_end = firebase_private_key[-25:] if len(firebase_private_key) > 25 else ""
            logger.info(f"Private key format: starts with '{key_start}...' ends with '...{key_end}' (length: {len(firebase_private_key)})")
                
            # Verify key format
            if not firebase_private_key.strip().startswith("-----BEGIN PRIVATE KEY-----"):
                logger.error("Private key does not start with correct header")
                
            if not firebase_private_key.strip().endswith("-----END PRIVATE KEY-----"):
                logger.error("Private key does not end with correct footer")
            
        # Check if we have the minimum required env variables or force_init is used
        if (firebase_private_key and firebase_client_email) or force_init:
            logger.info(f"Attempting Firebase initialization with environment variables (force_init: {force_init}) for project: {firebase_project_id}")
            
            # Create complete credential object from env variables
            cred_dict = {
                "type": os.environ.get("FIREBASE_TYPE", "service_account"),
                "project_id": firebase_project_id,
                "private_key_id": os.environ.get("FIREBASE_PRIVATE_KEY_ID", ""),
                "private_key": firebase_private_key,
                "client_email": firebase_client_email,
                "client_id": os.environ.get("FIREBASE_CLIENT_ID", ""),
                "auth_uri": os.environ.get("FIREBASE_AUTH_URI", "https://accounts.google.com/o/oauth2/auth"),
                "token_uri": os.environ.get("FIREBASE_TOKEN_URI", "https://oauth2.googleapis.com/token"),
                "auth_provider_x509_cert_url": os.environ.get("FIREBASE_AUTH_PROVIDER_CERT_URL", "https://www.googleapis.com/oauth2/v1/certs"),
                "client_x509_cert_url": os.environ.get("FIREBASE_CLIENT_CERT_URL", "")
            }
            
            try:
                logger.info("Attempting to create Firebase credentials certificate")
                cred = credentials.Certificate(cred_dict)
                logger.info("Certificate created successfully")
                
                # Initialize app
                logger.info("Initializing Firebase app...")
                firebase_admin.initialize_app(cred, {
                    'projectId': firebase_project_id,
                })
                logger.info("Firebase app initialized successfully")
                
                # Create Firestore client
                logger.info("Creating Firestore client...")
                db = firestore.client()
                logger.info("Firebase initialized successfully with admin credentials from environment variables")
                return db
            except Exception as e:
                logger.error(f"Error during Firebase initialization with environment variables: {str(e)}")
                logger.error(traceback.format_exc())
                handle_firebase_error(e)
                # Don't return here, allow fallback if db is still None
            
        # In Google Cloud (or with Application Default Credentials available)
        if db is None and (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or (settings.ENVIRONMENT == "production" and not force_init)):
            logger.info("Initializing Firebase with application default credentials")
            try:
                # Initialize with specific project ID
                options = {"projectId": CORRECT_PROJECT_ID}
                firebase_admin.initialize_app(options=options)
                db = firestore.client()
                logger.info(f"Firebase initialized successfully with application default credentials (project: {CORRECT_PROJECT_ID})")
                return db
            except Exception as e:
                logger.error(f"Error with application default credentials: {str(e)}")
                logger.error(traceback.format_exc())
                handle_firebase_error(e)
            
        # Check if db is still None after all attempts
        if db is None:
            logger.error("No Firebase credentials available or all initialization methods failed.")
            raise ValueError("Firebase admin credentials not found or initialization failed. Please set environment variables or provide a valid credentials file.")
            
        return db
        
    except Exception as e:
        logger.error(f"Unhandled exception in Firebase initialization: {str(e)}")
        logger.error(traceback.format_exc())
        handle_firebase_error(e)
        raise

def handle_firebase_error(e):
    """Handle common Firebase errors with helpful messages"""
    error_msg = str(e)
    if "Cloud Firestore API has not been used" in error_msg or "is disabled" in error_msg:
        logger.error("ERROR: You need to enable the Firestore API in your Google Cloud project!")
        logger.error("Go to: https://console.cloud.google.com/apis/library/firestore.googleapis.com")
        logger.error("Select your project and click 'Enable'")
    elif "The database (default) does not exist" in error_msg:
        logger.error("ERROR: You need to create a Firestore database for your project!")
        logger.error("Go to: https://console.firebase.google.com/project/crwlr-server-ccfd2/firestore")
        logger.error("Select your project and click 'Create Database'")
        logger.error("You can choose either Native or Datastore mode, but 'Native' is recommended")
    elif "Permission denied" in error_msg or "insufficient permissions" in error_msg:
        logger.error("ERROR: The service account lacks sufficient permissions!")
        logger.error("Ensure the service account has the 'Firebase Admin SDK Administrator Service Agent' role")
        logger.error("Go to: https://console.cloud.google.com/iam-admin/iam")
    elif "Could not deserialize key data" in error_msg:
        logger.error("ERROR: Invalid private key format. The key may be malformed or incorrectly encoded.")
        logger.error("Check that your FIREBASE_PRIVATE_KEY environment variable contains a valid private key")
        logger.error("The key should begin with '-----BEGIN PRIVATE KEY-----' and end with '-----END PRIVATE KEY-----'")
    else:
        logger.error(f"Error initializing Firebase: {error_msg}")

# Initialize Firebase on module import
try:
    logger.info("Starting Firebase initialization on module import...")
    # In development environment, force init even with missing vars
    force_init = settings.ENVIRONMENT == "development"
    db = initialize_firebase(force_init=force_init)
    if db:
        logger.info("Firebase initialized successfully - db object created")
    else:
        logger.error("Firebase initialization completed but db is None")
except Exception as e:
    logger.error(f"Failed to initialize Firebase: {str(e)}")
    logger.error(traceback.format_exc())
    db = None 