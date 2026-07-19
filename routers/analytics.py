from fastapi import APIRouter
from database import fetch_all, fetch_one

router = APIRouter()


@router.get("/outreach_summary")
def outreach_summary():
    """
    Métricas de performance por versión de mensaje en message_matrix.
    Muestra solo versiones con al menos 1 envío.
    Ordena por tipo y tasa de respuesta DESC → identifica qué combinaciones funcionan mejor.
    Útil para decidir qué mensajes mejorar o reemplazar (informa la Matriz 3).
    """
    rows = fetch_all(
        """
        SELECT
            m.id,
            m.tipo,
            m.vertical,
            m.version,
            m.fecha_desde,
            m.emails_enviados,
            m.emails_respondidos,
            m.tasa_respuesta
        FROM message_matrix m
        WHERE m.emails_enviados > 0
          AND m.activo = TRUE
        ORDER BY m.tipo, m.tasa_respuesta DESC NULLS LAST, m.emails_enviados DESC
        """,
        ()
    )

    if not rows:
        return {"mensaje": "Aún no hay envíos registrados.", "data": []}

    return {
        "data": [dict(r) for r in rows],
        "total_versiones": len(rows),
        "total_enviados": sum(r['emails_enviados'] for r in rows),
        "total_respondidos": sum(r['emails_respondidos'] for r in rows),
    }


@router.get("/weekly_progress")
def weekly_progress():
    """
    Devuelve X: cantidad de intentos primer_contacto enviados esta semana.
    Toda la fuente de leads es Apollo (lead_id siempre NULL) — ya no hay que cruzar
    con leads_brutos.

    Usado por el Flujo A (Planner Diario) para calcular N = min(ceil((30-X)/D), 15).
    """
    result = fetch_one(
        """
        SELECT COUNT(*) AS x
        FROM outreach_intentos oi
        WHERE oi.tipo = 'primer_contacto'
          AND oi.estado IN ('pendiente', 'enviado', 'respondio', 'bounce_hard', 'bounce_soft')
          AND oi.creado_at >= date_trunc('week', NOW())
        """,
        ()
    )
    x = result['x'] if result else 0
    return {
        "x": x,
        "description": "Intentos primer_contacto enviados esta semana",
        "semana_inicio": "lunes de la semana actual (date_trunc week)",
    }
