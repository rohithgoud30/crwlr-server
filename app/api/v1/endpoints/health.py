from fastapi import APIRouter

router = APIRouter()


@router.get("/health", status_code=200)
def health_check() -> dict:
    """
    Health check endpoint.
    Returns a simple response to indicate the API is running.
    """
    return {"status": "healthy", "message": "CRWLR API is running!"} 