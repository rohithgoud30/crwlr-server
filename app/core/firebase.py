import os
import logging
import json
import firebase_admin
from firebase_admin import credentials, firestore
from app.core.config import settings

# Setup logging
logger = logging.getLogger(__name__)

# Initialize db object for use by other modules
db = None

# Initialize Firebase
def initialize_firebase():
    """Initialize Firebase Admin SDK with proper admin credentials"""
    global db  # Access the global db variable
    
    try:
        # Check if already initialized
        if firebase_admin._apps:
            logger.info("Firebase already initialized")
            if db is None:
                try:
                    db = firestore.client()
                except Exception as e:
                    handle_firebase_error(e)
            return db
        
        # OVERRIDE: Force the correct project ID regardless of environment variables
        CORRECT_PROJECT_ID = "crwlr-server-ccfd2"
        logger.info(f"Using project ID: {CORRECT_PROJECT_ID} (hardcoded for testing)")
        
        # Check if environment variables for service account are available
        firebase_project_id = CORRECT_PROJECT_ID  # Override with the correct ID
        firebase_private_key = os.environ.get("FIREBASE_PRIVATE_KEY", "").replace("\\n", "\n")
        firebase_client_email = os.environ.get("FIREBASE_CLIENT_EMAIL")
            
        # Check if we have the minimum required env variables
        if firebase_private_key and firebase_client_email:
            logger.info(f"Initializing Firebase with environment variables for project: {firebase_project_id}")
            
            # Create credential object from env variables
            cred_dict = {
                "type": "service_account",
                "project_id": firebase_project_id,
                "private_key": firebase_private_key,
                "client_email": firebase_client_email,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
            }
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred, {
                'projectId': firebase_project_id,
            })
            try:
                db = firestore.client()
                logger.info("Firebase initialized successfully with admin credentials from environment variables")
            except Exception as e:
                handle_firebase_error(e)
            
        # If a service account file exists, try to use that
        elif os.path.exists("firebase-credentials.json"):
            logger.info("Initializing Firebase with admin service account file")
            cred = credentials.Certificate("firebase-credentials.json")
            firebase_admin.initialize_app(cred)
            try:
                db = firestore.client()
                logger.info("Firebase initialized successfully with admin service account JSON file")
            except Exception as e:
                handle_firebase_error(e)
        
        # Check for explicit credentials file path
        elif os.path.exists("firebase-admin-credentials.json"):
            logger.info("Initializing Firebase with explicit admin credentials file")
            cred = credentials.Certificate("firebase-admin-credentials.json")
            firebase_admin.initialize_app(cred)
            try:
                db = firestore.client()
                logger.info("Firebase initialized successfully with explicit admin credentials file")
            except Exception as e:
                handle_firebase_error(e)
            
        # In Google Cloud (or with Application Default Credentials available)
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or settings.ENVIRONMENT == "production":
            logger.info("Initializing Firebase with application default credentials")
            # Initialize with specific project ID
            options = {"projectId": CORRECT_PROJECT_ID}
            firebase_admin.initialize_app(options=options)
            try:
                db = firestore.client()
                logger.info(f"Firebase initialized successfully with application default credentials (project: {CORRECT_PROJECT_ID})")
            except Exception as e:
                handle_firebase_error(e)
            
        else:
            logger.error("No Firebase credentials available")
            raise ValueError("Firebase admin credentials not found. Please set environment variables or provide a credentials file.")
            
        return db
        
    except Exception as e:
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
    else:
        logger.error(f"Error initializing Firebase: {error_msg}")

# Initialize Firebase on module import
try:
    db = initialize_firebase()
except Exception as e:
    logger.error(f"Failed to initialize Firebase: {str(e)}")
    db = None 