from fastapi import APIRouter, Depends

from app.api.v1.endpoints import tos, privacy, extract, summary
from app.core.auth import get_api_key

api_router = APIRouter(dependencies=[Depends(get_api_key)])
api_router.include_router(tos.router, tags=["legal"])
api_router.include_router(privacy.router, tags=["legal"])
api_router.include_router(extract.router, tags=["content"])
api_router.include_router(summary.router, tags=["content"]) 