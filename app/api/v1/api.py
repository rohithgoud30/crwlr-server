from fastapi import APIRouter

from app.api.v1.endpoints import health, tos, privacy, extract, summary

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(tos.router, tags=["legal"])
api_router.include_router(privacy.router, tags=["legal"])
api_router.include_router(extract.router, tags=["content"])
api_router.include_router(summary.router, tags=["content"]) 