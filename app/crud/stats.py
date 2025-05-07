from typing import Dict, Any, Optional
import logging
from app.crud.firebase_base import FirebaseCRUDBase
from app.core.database import db
from datetime import datetime
from google.cloud import firestore
from google.cloud.firestore_v1.transaction import Transaction

# Setup logging
logger = logging.getLogger(__name__)

class StatsCRUD(FirebaseCRUDBase):
    """CRUD for document statistics management."""
    
    def __init__(self):
        """Initialize with stats collection."""
        super().__init__("stats")
        self.stats_id = "global_stats"
        # Set a flag to track if Firebase is working
        self.firebase_operational = self.collection is not None
        if not self.firebase_operational:
            logger.error("StatsCRUD initialized with non-functional Firebase connection")
    
    async def get_document_counts(self) -> Dict[str, Any]:
        """Get document counts from the stats collection."""
        try:
            # First check if Firebase is available
            if not self.collection:
                logger.error("Firebase not initialized - returning default document counts")
                return {
                    "tos_count": 0,
                    "pp_count": 0,
                    "total_count": 0,
                    "last_updated": None
                }
            
            # Get the stats document
            doc_ref = self.collection.document(self.stats_id)
            doc = doc_ref.get()
            
            if doc.exists:
                # Stats document exists, return the counts
                stats = doc.to_dict()
                return {
                    "tos_count": stats.get("tos_count", 0),
                    "pp_count": stats.get("pp_count", 0),
                    "total_count": stats.get("total_count", 0),
                    "last_updated": stats.get("last_updated")
                }
            else:
                # Stats document doesn't exist yet, create it by counting documents
                logger.info("Stats document not found, creating it by counting documents")
                counts = await self.initialize_stats()
                return counts
                
        except Exception as e:
            logger.error(f"Error getting document counts from stats: {str(e)}")
            # Return default counts when an error occurs
            return {
                "tos_count": 0,
                "pp_count": 0,
                "total_count": 0,
                "last_updated": None
            }
    
    async def initialize_stats(self) -> Dict[str, Any]:
        """Initialize stats by counting all documents."""
        try:
            # Get a reference to the documents collection
            docs_collection = db.collection("documents")
            
            # Count ToS documents
            tos_query = docs_collection.where("document_type", "==", "tos")
            tos_docs = list(tos_query.stream())
            tos_count = len(tos_docs)
            
            # Count PP documents
            pp_query = docs_collection.where("document_type", "==", "pp")
            pp_docs = list(pp_query.stream())
            pp_count = len(pp_docs)
            
            # Total count
            total_count = tos_count + pp_count
            
            # Get current time for last_updated
            now = datetime.now()
            
            # Create the stats document
            stats_data = {
                "tos_count": tos_count,
                "pp_count": pp_count,
                "total_count": total_count,
                "last_updated": now
            }
            
            # Save to Firestore
            self.collection.document(self.stats_id).set(stats_data)
            logger.info(f"Stats initialized: ToS={tos_count}, PP={pp_count}, Total={total_count}")
            
            return {
                "tos_count": tos_count,
                "pp_count": pp_count,
                "total_count": total_count,
                "last_updated": now
            }
        except Exception as e:
            logger.error(f"Error initializing stats: {str(e)}")
            return {
                "tos_count": 0,
                "pp_count": 0,
                "total_count": 0,
                "last_updated": None
            }
    
    async def increment_document_count(self, document_type: str) -> bool:
        """Increment the count for a specific document type."""
        if not self.collection:
            logger.error("Firebase not initialized - cannot increment count")
            return False
            
        try:
            # Get document reference
            doc_ref = self.collection.document(self.stats_id)
            
            # Use transaction to ensure atomic update
            transaction = db.transaction()
            
            @firestore.transactional
            def update_in_transaction(transaction: Transaction, doc_ref):
                doc = doc_ref.get(transaction=transaction)
                
                if not doc.exists:
                    # Document doesn't exist, initialize it
                    stats_data = {
                        "tos_count": 1 if document_type == "tos" else 0,
                        "pp_count": 1 if document_type == "pp" else 0,
                        "total_count": 1,
                        "last_updated": datetime.now()
                    }
                    transaction.set(doc_ref, stats_data)
                else:
                    # Document exists, update counts
                    stats = doc.to_dict()
                    tos_count = stats.get("tos_count", 0)
                    pp_count = stats.get("pp_count", 0)
                    
                    if document_type == "tos":
                        tos_count += 1
                    elif document_type == "pp":
                        pp_count += 1
                    
                    total_count = tos_count + pp_count
                    
                    transaction.update(doc_ref, {
                        "tos_count": tos_count,
                        "pp_count": pp_count,
                        "total_count": total_count,
                        "last_updated": datetime.now()
                    })
                
                return True
            
            # Execute the transaction
            result = update_in_transaction(transaction, doc_ref)
            logger.info(f"Incremented count for document type: {document_type}")
            return result
            
        except Exception as e:
            logger.error(f"Error incrementing document count: {str(e)}")
            return False
    
    async def decrement_document_count(self, document_type: str) -> bool:
        """Decrement the count for a specific document type."""
        if not self.collection:
            logger.error("Firebase not initialized - cannot decrement count")
            return False
            
        try:
            # Get document reference
            doc_ref = self.collection.document(self.stats_id)
            
            # Use transaction to ensure atomic update
            transaction = db.transaction()
            
            @firestore.transactional
            def update_in_transaction(transaction: Transaction, doc_ref):
                doc = doc_ref.get(transaction=transaction)
                
                if not doc.exists:
                    # Document doesn't exist, initialize it with zeros
                    stats_data = {
                        "tos_count": 0,
                        "pp_count": 0,
                        "total_count": 0,
                        "last_updated": datetime.now()
                    }
                    transaction.set(doc_ref, stats_data)
                    return True
                
                # Document exists, update counts
                stats = doc.to_dict()
                tos_count = max(0, stats.get("tos_count", 0) - (1 if document_type == "tos" else 0))
                pp_count = max(0, stats.get("pp_count", 0) - (1 if document_type == "pp" else 0))
                total_count = tos_count + pp_count
                
                transaction.update(doc_ref, {
                    "tos_count": tos_count,
                    "pp_count": pp_count,
                    "total_count": total_count,
                    "last_updated": datetime.now()
                })
                
                return True
            
            # Execute the transaction
            result = update_in_transaction(transaction, doc_ref)
            logger.info(f"Decremented count for document type: {document_type}")
            return result
            
        except Exception as e:
            logger.error(f"Error decrementing document count: {str(e)}")
            return False

# Create a global instance for reuse
stats_crud = StatsCRUD() 