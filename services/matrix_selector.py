from typing import Optional
from database import fetch_one, execute


def _normalizar_vertical(vertical_raw: str) -> str:
    """
    apollo_leads.vertical trae la industria cruda de Apollo (texto en inglés, ej.
    "information technology & services") o, para leads que ya vienen clasificados,
    directamente un vertical_consolidada en español (ej. "salud").

    vertical_alias_map traduce ambos casos a los 13 verticales consolidados del negocio
    (ver knowledge/comercial). Si no hay alias conocido, se usa el valor tal cual llegó —
    y si tampoco matchea nada en message_matrix, get_context() cae a 'sin_clasificar'.
    """
    if not vertical_raw:
        return 'sin_clasificar'
    row = fetch_one(
        "SELECT vertical_consolidada FROM vertical_alias_map WHERE lower(vertical_raw) = lower(%s)",
        (vertical_raw,)
    )
    return row['vertical_consolidada'] if row else vertical_raw


def get_context(tipo: str, vertical: str, version: str = 'A') -> Optional[dict]:
    """
    Busca la fila de contexto en message_matrix para un vertical dado.
    message_matrix ya NO es la fuente literal del mensaje — es un banco de ejemplos/ángulo
    por vertical que n8n consulta antes de armar el prompt de Gemini (build_gemini_prompt_apollo),
    para darle tono y un pain-point de referencia. Gemini genera el asunto y el cuerpo reales.

    Granularidad: solo por vertical (ya no por stack_categoria — el stack real del lead ya lo
    tiene Gemini disponible vía apollo_leads.tech_stack_apollo/tech_stack_wappalyzer).

    `vertical` puede venir crudo de Apollo (inglés) — se normaliza vía vertical_alias_map
    antes de buscar. Fallback final: vertical='sin_clasificar'.
    """
    vertical_consolidada = _normalizar_vertical(vertical)

    query = """
        SELECT id, tipo, vertical, version, asunto, cuerpo
        FROM message_matrix
        WHERE tipo = %s AND vertical = %s AND version = %s AND activo = TRUE
        LIMIT 1
    """
    result = fetch_one(query, (tipo, vertical_consolidada, version))
    if result:
        return dict(result)

    if vertical_consolidada != 'sin_clasificar':
        result = fetch_one(query, (tipo, 'sin_clasificar', version))
        return dict(result) if result else None

    return None


def increment_sent(matrix_id: int):
    execute(
        "UPDATE message_matrix SET emails_enviados = emails_enviados + 1 WHERE id = %s",
        (matrix_id,)
    )


def increment_replied(matrix_id: int):
    execute(
        "UPDATE message_matrix SET emails_respondidos = emails_respondidos + 1 WHERE id = %s",
        (matrix_id,)
    )
