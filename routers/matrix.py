from fastapi import APIRouter, HTTPException
from services.matrix_selector import get_context

router = APIRouter()


@router.get("/context/{tipo}/{vertical}")
def get_matrix_context(tipo: str, vertical: str, version: str = 'A'):
    """
    Devuelve la fila de contexto (ángulo/ejemplo de tono) de message_matrix para un vertical.
    Usado por n8n (nodo build_gemini_prompt_apollo) ANTES de llamar a Gemini, para inyectar
    el pain-point típico de ese vertical como inspiración del prompt — no se manda literal
    al prospecto, Gemini redacta el asunto y el cuerpo reales.
    """
    ctx = get_context(tipo, vertical, version)
    if not ctx:
        raise HTTPException(
            status_code=404,
            detail=f"No hay contexto en matrix para tipo={tipo}, vertical={vertical}, version={version}"
        )
    return ctx
