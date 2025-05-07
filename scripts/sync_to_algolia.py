#!/usr/bin/env python3
"""
Script to synchronize Firebase documents to Algolia for faster search.

This script:
1. Fetches all documents from Firebase
2. Prepares them for Algolia indexing (including cleanup)
3. Sends them to Algolia in batches

Usage:
    python sync_to_algolia.py [--limit N] [--doc-type tos|pp]

Options:
    --limit N       Limit to N documents (default: all)
    --doc-type TYPE Only sync documents of specified type ('tos' or 'pp')
"""

import os
import sys
import argparse
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

# Add the parent directory to the path so we can import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import db
from app.core.algolia import batch_save_documents, init_algolia

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("sync_to_algolia")

async def get_documents(doc_type: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Fetch documents from Firebase.
    
    Args:
        doc_type: Optional document type filter ('tos' or 'pp')
        limit: Optional limit on number of documents
        
    Returns:
        List of document dictionaries
    """
    try:
        # Get the documents collection
        collection = db.collection("documents")
        
        # Build query
        query = collection
        if doc_type:
            query = query.where("document_type", "==", doc_type)
            
        # Apply limit if specified
        if limit:
            query = query.limit(limit)
            
        # Execute query
        docs = list(query.stream())
        
        # Convert to dictionaries with ID
        results = []
        for doc in docs:
            doc_dict = doc.to_dict()
            doc_dict["id"] = doc.id
            results.append(doc_dict)
            
        logger.info(f"Retrieved {len(results)} documents from Firebase")
        return results
    except Exception as e:
        logger.error(f"Error fetching documents from Firebase: {e}")
        return []

def prepare_documents_for_algolia(docs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Prepare documents for Algolia indexing.
    
    Args:
        docs: List of document dictionaries
        
    Returns:
        Dictionary with 'tos' and 'pp' keys, each containing a list of prepared documents
    """
    tos_docs = []
    pp_docs = []
    
    for doc in docs:
        # Make a copy to avoid modifying the original
        algolia_doc = doc.copy()
        
        # Set objectID to document ID
        if "id" in algolia_doc:
            algolia_doc["objectID"] = algolia_doc["id"]
            
        # Limit raw_text to 5KB to stay within Algolia record size limits
        if "raw_text" in algolia_doc and algolia_doc["raw_text"]:
            algolia_doc["raw_text"] = algolia_doc["raw_text"][:5000]
            
        # Convert dates to ISO format strings
        for date_field in ["created_at", "updated_at"]:
            if date_field in algolia_doc and isinstance(algolia_doc[date_field], datetime):
                algolia_doc[date_field] = algolia_doc[date_field].isoformat()
                
        # Sort into the right document type
        doc_type = algolia_doc.get("document_type", "").lower()
        if doc_type == "tos":
            tos_docs.append(algolia_doc)
        elif doc_type == "pp":
            pp_docs.append(algolia_doc)
        else:
            logger.warning(f"Unknown document type: {doc_type}, ID: {algolia_doc.get('id')}")
            
    logger.info(f"Prepared {len(tos_docs)} TOS documents and {len(pp_docs)} Privacy Policy documents for Algolia")
    return {
        "tos": tos_docs,
        "pp": pp_docs
    }

async def sync_to_algolia(doc_type: Optional[str] = None, limit: Optional[int] = None):
    """
    Main synchronization function.
    
    Args:
        doc_type: Optional document type filter ('tos' or 'pp')
        limit: Optional limit on number of documents
    """
    logger.info(f"Starting sync to Algolia. Doc type: {doc_type}, Limit: {limit}")
    
    # Initialize Algolia
    algolia_client = init_algolia()
    if not algolia_client:
        logger.error("Failed to initialize Algolia client. Check your credentials.")
        return
        
    # Fetch documents from Firebase
    docs = await get_documents(doc_type, limit)
    if not docs:
        logger.warning("No documents found in Firebase or error fetching documents")
        return
        
    # Prepare documents for Algolia
    prepared_docs = prepare_documents_for_algolia(docs)
    
    # Sync TOS documents if requested
    if not doc_type or doc_type.lower() == "tos":
        tos_docs = prepared_docs["tos"]
        if tos_docs:
            logger.info(f"Syncing {len(tos_docs)} TOS documents to Algolia")
            result = batch_save_documents(tos_docs, "tos")
            if result:
                logger.info(f"Successfully synced {len(tos_docs)} TOS documents to Algolia")
            else:
                logger.error("Failed to sync TOS documents to Algolia")
        else:
            logger.info("No TOS documents to sync")
            
    # Sync Privacy Policy documents if requested
    if not doc_type or doc_type.lower() == "pp":
        pp_docs = prepared_docs["pp"]
        if pp_docs:
            logger.info(f"Syncing {len(pp_docs)} Privacy Policy documents to Algolia")
            result = batch_save_documents(pp_docs, "pp")
            if result:
                logger.info(f"Successfully synced {len(pp_docs)} Privacy Policy documents to Algolia")
            else:
                logger.error("Failed to sync Privacy Policy documents to Algolia")
        else:
            logger.info("No Privacy Policy documents to sync")
            
    logger.info("Sync to Algolia complete")

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Sync Firebase documents to Algolia")
    parser.add_argument("--limit", type=int, help="Limit number of documents to sync")
    parser.add_argument("--doc-type", choices=["tos", "pp"], help="Only sync documents of this type")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    
    # Run the sync operation
    asyncio.run(sync_to_algolia(args.doc_type, args.limit)) 