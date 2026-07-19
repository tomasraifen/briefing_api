from fastapi import APIRouter
from models.webhook import InstantlyWebhook

router = APIRouter()


@router.post("/instantly")
def handle_instantly_webhook(payload: InstantlyWebhook):
    """
    DESACTIVADO — Instantly cancelado. Acepta el POST para no romper webhooks
    configurados en Instantly pero no procesa nada ni escribe a BD.
    Eliminar este endpoint una vez que Instantly esté dado de baja definitivamente.
    """
    return {"status": "ok"}