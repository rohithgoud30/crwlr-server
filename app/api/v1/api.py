from fastapi import APIRouter, Depends, Response

from app.api.v1.endpoints import tos, privacy, extract, summary, crawl, textmining, wordfrequency, company_info, documents
from app.core.auth import get_api_key

# Main API router with authentication
api_router = APIRouter(dependencies=[Depends(get_api_key)])
api_router.include_router(tos.router, tags=["legal"])
api_router.include_router(privacy.router, tags=["legal"])
api_router.include_router(extract.router, tags=["content"])
api_router.include_router(summary.router, tags=["content"])
api_router.include_router(crawl.router, tags=["crawl"])
api_router.include_router(textmining.router, tags=["analysis"])
api_router.include_router(wordfrequency.router, tags=["analysis"])
api_router.include_router(company_info.router, tags=["company"])
api_router.include_router(documents.router, tags=["documents"])

# Test router without authentication for debugging
test_router = APIRouter()

@test_router.get("/", tags=["test"])
async def test_endpoint():
    """
    Test endpoint without authentication for debugging purposes.
    """
    return {"status": "ok", "message": "Server is running"} 