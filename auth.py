from fastapi import HTTPException, Header
from typing import Optional

from config import get_settings


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """
    Dependencia compartida: exige el header X-API-Key en todos los routers salvo /health.
    n8n manda esta clave en cada llamada (ver .env.example / API_KEY).
    """
    settings = get_settings()
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="API key inválida o ausente (header X-API-Key)")
