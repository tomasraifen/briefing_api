from fastapi import APIRouter
from database import fetch_one

router = APIRouter()


@router.get("/health")
def health_check():
    try:
        fetch_one("SELECT 1 AS ok")
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "database": db_status,
        "service": "briefing_api",
        "version": "2.0.0",
    }
