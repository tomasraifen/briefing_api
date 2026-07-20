from fastapi import APIRouter, Query
from datetime import datetime, timedelta
from typing import Optional
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


@router.get("/audit_outreach")
def audit_outreach(desde: Optional[str] = Query(default=None), hasta: Optional[str] = Query(default=None)):
    """
    Auditoría de cold outreach para el periodo [desde, hasta] (default: últimos 14 días).

    Fuente: outreach_intentos en Postgres -- estado real y autoritativo del sistema, ya que
    Flujo 4 (Dispatcher), Flujo 5 (Unibox) y Flujo 6 (Bounce Audit) escriben enviado_at /
    respondio_at / bounce_at ahí mismo. No se re-deriva nada leyendo la bandeja de Gmail --
    el flujo viejo (scraping in:sent / from:mailer-daemon / threadId) dependía de cómo Gmail
    agrupa hilos, y desde que el seguimiento se manda como correo nuevo (no threadeado) esa
    lógica quedaba rota para "seguimientos enviados" (siempre daba 0).

    Usado por Flujo 8 -- Auditoria Cold Outreach.
    """
    hasta_dt = datetime.fromisoformat(hasta) if hasta else datetime.utcnow()
    desde_dt = datetime.fromisoformat(desde) if desde else hasta_dt - timedelta(days=14)
    hasta_fin = hasta_dt + timedelta(days=1)  # inclusivo del día "hasta"

    enviados = fetch_all(
        """
        SELECT tipo, asunto, email_destino
        FROM outreach_intentos
        WHERE enviado_at >= %s AND enviado_at < %s
        """,
        (desde_dt, hasta_fin)
    )
    bounces = fetch_all(
        """
        SELECT email_destino
        FROM outreach_intentos
        WHERE estado = 'bounce_hard' AND bounce_at >= %s AND bounce_at < %s
        """,
        (desde_dt, hasta_fin)
    )
    respuestas = fetch_all(
        """
        SELECT id
        FROM outreach_intentos
        WHERE estado = 'respondio' AND respondio_at >= %s AND respondio_at < %s
        """,
        (desde_dt, hasta_fin)
    )

    total_enviados = len(enviados)
    primer_contacto = sum(1 for e in enviados if e['tipo'] == 'primer_contacto')
    seguimientos = sum(1 for e in enviados if e['tipo'] == 'seguimiento')
    total_bounces = len(bounces)
    total_respuestas = len(respuestas)
    emails_entregados = total_enviados - total_bounces

    tasa_rebote = round((total_bounces / total_enviados) * 100, 2) if total_enviados else 0
    tasa_entrega = round((emails_entregados / total_enviados) * 100, 2) if total_enviados else 0
    tasa_respuesta = round((total_respuestas / total_enviados) * 100, 2) if total_enviados else 0

    asunto_count: dict = {}
    for e in enviados:
        asunto_count[e['asunto']] = asunto_count.get(e['asunto'], 0) + 1
    asuntos_enviados = sorted(
        ({"asunto": a, "enviados": c} for a, c in asunto_count.items()),
        key=lambda x: -x['enviados']
    )

    dominio_count: dict = {}
    for b in bounces:
        dominio = (b['email_destino'] or '').split('@')[-1]
        if dominio:
            dominio_count[dominio] = dominio_count.get(dominio, 0) + 1
    bounces_por_dominio = sorted(
        ({"dominio": d, "bounces": c} for d, c in dominio_count.items()),
        key=lambda x: -x['bounces']
    )

    diag = []
    if total_enviados == 0:
        diag.append('Sin emails enviados en el periodo - verificar fechas o estado del Flujo 4')
    else:
        diag.append(f'{total_enviados} emails enviados - {primer_contacto} primer contacto, {seguimientos} seguimientos')
        if tasa_rebote >= 10:
            diag.append(f'CRITICO: Tasa de rebote {tasa_rebote}% - pausar Flujo 4 y revisar la calidad de la lista')
        elif tasa_rebote >= 5:
            diag.append(f'ALTO: Tasa de rebote {tasa_rebote}% - revisar calidad de la lista antes de seguir')
        elif total_bounces > 0:
            diag.append(f'{total_bounces} bounces ({tasa_rebote}%) - dentro de rangos, limpiar BD preventivamente')
        else:
            diag.append('Sin bounces - lista limpia')
        if tasa_respuesta == 0:
            diag.append('Cero replies - revisar copy, subject lines y segmentacion')
        elif tasa_respuesta < 2:
            diag.append(f'Tasa de respuesta baja: {tasa_respuesta}% - optimizar copy')
        else:
            diag.append(f'Tasa de respuesta: {tasa_respuesta}% - dentro de benchmarks')

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "periodo": {"desde": desde_dt.date().isoformat(), "hasta": hasta_dt.date().isoformat()},
        "resumen": {
            "total_enviados": total_enviados,
            "primer_contacto": primer_contacto,
            "seguimientos_enviados": seguimientos,
            "emails_entregados": emails_entregados,
            "total_bounces": total_bounces,
            "dominios_afectados": len(dominio_count),
            "respuestas_recibidas": total_respuestas,
        },
        "tasas": {
            "tasa_rebote_pct": tasa_rebote,
            "tasa_entrega_pct": tasa_entrega,
            "tasa_respuesta_pct": tasa_respuesta,
        },
        "asuntos_enviados": asuntos_enviados,
        "bounces_por_dominio": bounces_por_dominio,
        "diagnostico": diag,
    }
