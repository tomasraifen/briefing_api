from fastapi import APIRouter, HTTPException, Header
from typing import Optional

from config import get_settings
from models.webhook import InstantlyWebhook

router = APIRouter()
settings = get_settings()


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="API key invalida")


@router.post("/instantly")
def handle_instantly_webhook(payload: InstantlyWebhook, x_api_key: Optional[str] = Header(None)):
    """
    DESACTIVADO — Instantly cancelado. Acepta el POST para no romper webhooks
    configurados en Instantly pero no procesa nada ni escribe a BD.
    Eliminar este endpoint una vez que Instantly esté dado de baja definitivamente.
    """
    return {"status": "ok"}