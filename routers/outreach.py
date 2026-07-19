from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
import httpx

from config import get_settings
from database import fetch_one, fetch_all, execute
from services.matrix_selector import increment_sent

router = APIRouter()
settings = get_settings()
IS_DEV = settings.environment == "development"


class MarkSentRequest(BaseModel):
    intento_id: int
    instantly_id: Optional[str] = None  # deprecated — era para Instantly. Vacío desde la migración a Gmail API.


class MarkBouncesRequest(BaseModel):
    email_destinos: list[str]  # lista de emails que hicieron bounce según Gmail


@router.get("/pending")
def get_pending_intentos():
    """
    Devuelve todos los intentos listos para enviar (estado='pendiente', programado_para <= NOW()).
    Toda la fuente de leads es Apollo (lead_id siempre NULL) — no hay filtro adicional por respuesta
    previa acá, eso lo maneja quien detecta el reply y cambia el estado del apollo_lead.

    Usado por el Flujo B (Dispatcher) cada 30 minutos.
    """
    intentos = fetch_all(
        """
        SELECT
            oi.id AS intento_id,
            oi.lead_id,
            oi.tipo,
            oi.email_destino,
            oi.asunto,
            oi.cuerpo,
            oi.matrix_id,
            oi.programado_para
        FROM outreach_intentos oi
        WHERE oi.estado = 'pendiente'
          AND oi.programado_para <= NOW()
        ORDER BY oi.programado_para ASC
        """,
        ()
    )
    return {
        "intentos": [dict(i) for i in intentos] if intentos else [],
        "total": len(intentos) if intentos else 0,
    }


@router.post("/mark_sent")
def mark_sent(req: MarkSentRequest):
    """
    Marca un intento como enviado tras el envío por Gmail API.
    Incrementa el contador emails_enviados en message_matrix (si el intento tiene matrix_id asociado).

    Usado por el Flujo B (Dispatcher) después de cada envío exitoso.
    """
    intento = fetch_one(
        "SELECT id, matrix_id FROM outreach_intentos WHERE id = %s",
        (req.intento_id,)
    )
    if not intento:
        raise HTTPException(status_code=404, detail=f"Intento {req.intento_id} no encontrado")

    execute(
        """
        UPDATE outreach_intentos
        SET estado = 'enviado', enviado_at = %s, instantly_id = %s
        WHERE id = %s
        """,
        (datetime.utcnow(), req.instantly_id or '', req.intento_id)
    )

    if intento['matrix_id']:
        increment_sent(intento['matrix_id'])

    return {"status": "ok", "intento_id": req.intento_id}


DAILY_CAP = 15
BUFFER_DIAS_HABILES = 5
MAX_POR_CORRIDA = 30  # tope de leads a generar en una sola corrida del Planner

# Países reales en apollo_leads (verificado contra la BD 2026-07-09) → código Nager.Date
PAIS_A_CODIGO = {
    'colombia': 'CO', 'mexico': 'MX', 'méxico': 'MX', 'chile': 'CL',
    'argentina': 'AR', 'peru': 'PE', 'perú': 'PE', 'ecuador': 'EC',
    'uruguay': 'UY', 'panama': 'PA', 'panamá': 'PA', 'guatemala': 'GT',
    'costa rica': 'CR', 'paraguay': 'PY', 'bolivia': 'BO', 'united states': 'US',
}


def _festivos_por_pais(anios: set[int]) -> dict:
    """
    Devuelve {codigo_pais: set(fechas festivas 'YYYY-MM-DD')} consultando
    Nager.Date una vez por país/año (fail-open: si la API falla para un país,
    se asume sin festivos conocidos ese año — no bloquea el pipeline).
    """
    codigos = set(PAIS_A_CODIGO.values())
    resultado = {c: set() for c in codigos}
    for anio in anios:
        for codigo in codigos:
            try:
                resp = httpx.get(
                    f"https://date.nager.at/api/v3/PublicHolidays/{anio}/{codigo}",
                    timeout=10,
                )
                resp.raise_for_status()
                resultado[codigo].update(h["date"] for h in resp.json())
            except Exception:
                pass
    return resultado


@router.get("/planner_batch")
def planner_batch():
    """
    Devuelve el batch completo ya armado para el Planner (Flujo A): cada lead viene con su
    programado_para asignado, respetando festivos por país (Nager.Date, por lead — no un solo
    país para todos) y el cap diario DAILY_CAP, manteniendo un colchón de BUFFER_DIAS_HABILES
    días hábiles.

    Por qué toda la asignación vive acá y no en n8n: así se puede probar contra la BD real sin
    depender de ejecutar el workflow, y evita que un festivo en un país bloquee el envío a leads
    de otro país ese mismo día.

    Días candidatos: los próximos BUFFER_DIAS_HABILES días hábiles (lunes-viernes) a partir de
    mañana. Si TODOS los países están festivos un día candidato, ese día se descarta por completo
    (no se cuenta como parte del horizonte). Auto-reparable: si un día quedó con hueco por una
    corrida fallida, la siguiente corrida lo rellena primero antes de extender el horizonte hacia
    adelante — el Planner nunca programa "hoy", solo hacia el futuro, y el Dispatcher solo envía
    lo que ya está programado.
    """
    hoy = datetime.utcnow().date()
    anios = {hoy.year, (hoy + timedelta(days=BUFFER_DIAS_HABILES * 3)).year}
    festivos = _festivos_por_pais(anios)

    dia = hoy
    candidatos = []
    intentos_dia = 0
    while len(candidatos) < BUFFER_DIAS_HABILES and intentos_dia < 30:
        dia += timedelta(days=1)
        intentos_dia += 1
        if dia.weekday() >= 5:
            continue
        fecha_str = dia.isoformat()
        paises_habiles = {c for c, fechas in festivos.items() if fecha_str not in fechas}
        if not paises_habiles:
            continue  # festivo en TODOS los países conocidos — día inválido, no cuenta
        candidatos.append(dia)

    dias_plan = []
    for candidato in candidatos:
        fecha_str = candidato.isoformat()
        paises_habiles = {c for c, fechas in festivos.items() if fecha_str not in fechas}
        result = fetch_one(
            """
            SELECT COUNT(*) AS n
            FROM outreach_intentos
            WHERE lead_id IS NULL AND tipo = 'primer_contacto'
              AND estado IN ('pendiente', 'enviado')
              AND programado_para::date = %s
            """,
            (candidato,)
        )
        ya_agendados = result['n'] if result else 0
        cupo = DAILY_CAP - ya_agendados
        if cupo <= 0:
            continue
        dias_plan.append({"fecha": candidato, "cupo": cupo, "paises": paises_habiles})

    if not dias_plan:
        extra = candidatos[-1] + timedelta(days=1) if candidatos else hoy + timedelta(days=1)
        while extra.weekday() >= 5:
            extra += timedelta(days=1)
        fecha_str = extra.isoformat()
        paises_habiles = {c for c, fechas in festivos.items() if fecha_str not in fechas} or set(PAIS_A_CODIGO.values())
        dias_plan.append({"fecha": extra, "cupo": min(DAILY_CAP, MAX_POR_CORRIDA), "paises": paises_habiles})

    # Traer candidatos de sobra para poder repartir por país sin quedarnos cortos
    pendientes = fetch_all(
        """
        SELECT id, apollo_id, nombre_decisor, cargo, email, empresa, vertical,
               pais, ciudad, empleados, stack_categoria, tech_stack_apollo,
               tech_stack_wappalyzer, company_brief, news_snippet, apollo_score_angulo
        FROM apollo_leads WHERE estado = 'pendiente' LIMIT 500
        """,
        ()
    )

    asignados = []
    sin_dia_habil = 0
    restante_total = MAX_POR_CORRIDA
    for lead in pendientes:
        if restante_total <= 0:
            break
        codigo = PAIS_A_CODIGO.get((lead.get('pais') or 'colombia').strip().lower())
        if not codigo:
            continue
        destino = next((d for d in dias_plan if d['cupo'] > 0 and codigo in d['paises']), None)
        if not destino:
            sin_dia_habil += 1
            continue
        destino['cupo'] -= 1
        restante_total -= 1
        programado = datetime.combine(destino['fecha'], datetime.min.time()).replace(hour=14, minute=0)
        lead_dict = dict(lead)
        lead_dict['programado_para'] = programado.isoformat()
        asignados.append(lead_dict)

    return {
        "leads": asignados,
        "total": len(asignados),
        "sin_dia_habil_disponible": sin_dia_habil,
        "dias_considerados": [d["fecha"].isoformat() for d in dias_plan],
    }


@router.post("/prepare_apollo")
def prepare_outreach_apollo(req: dict):
    """
    Registra el intento de outreach para un lead Apollo. Gemini genera SIEMPRE tanto el asunto
    (asunto_gemini) como el cuerpo (email_cuerpo) — ya no hay fallback a message_matrix como
    fuente literal del mensaje. message_matrix pasó a ser banco de contexto/tono por vertical
    que n8n consulta ANTES de llamar a Gemini (ver GET /matrix/context/{tipo}/{vertical}),
    no una plantilla que se envía tal cual.

    programado_para (opcional): fecha/hora ISO a la que debe agendarse el envío (buffer
    multi-día armado por /outreach/planner_batch). Si no viene, se agenda para ahora mismo.
    """
    apollo_lead_id = req.get("apollo_lead_id")
    tipo = req.get("tipo", "primer_contacto")
    asunto_gemini = (req.get("asunto_gemini") or "").strip()
    email_cuerpo = (req.get("email_cuerpo") or "").strip()

    if len(asunto_gemini) < 5:
        raise HTTPException(status_code=422, detail="asunto_gemini es obligatorio (generado por Gemini)")
    if len(email_cuerpo) < 50:
        raise HTTPException(status_code=422, detail="email_cuerpo es obligatorio (generado por Gemini)")

    lead = fetch_one(
        "SELECT * FROM apollo_leads WHERE id = %s AND estado = 'pendiente'",
        (apollo_lead_id,)
    )
    if not lead:
        raise HTTPException(status_code=404, detail=f"Apollo lead {apollo_lead_id} no encontrado o ya procesado")

    programado_para_raw = req.get("programado_para")
    ahora = datetime.utcnow()
    try:
        programado_para = datetime.fromisoformat(programado_para_raw) if programado_para_raw else ahora
    except ValueError:
        programado_para = ahora

    # matrix_id opcional: si n8n usó GET /matrix/context/{tipo}/{vertical} para armar el prompt,
    # puede pasar el id de la fila de contexto usada, solo para trazabilidad de analytics.
    matrix_context_id = req.get("matrix_context_id")

    result = execute(
        """
        INSERT INTO outreach_intentos
            (lead_id, matrix_id, tipo, email_destino, asunto, cuerpo, estado, programado_para)
        VALUES (NULL, %s, %s, %s, %s, %s, 'pendiente', %s)
        RETURNING id
        """,
        (matrix_context_id, tipo, lead['email'], asunto_gemini, email_cuerpo, programado_para)
    )
    intento_id = result['id']

    execute(
        "UPDATE apollo_leads SET outreach_intento_id = %s, estado = 'enviado', contactado_at = %s WHERE id = %s",
        (intento_id, ahora, apollo_lead_id)
    )
    if matrix_context_id:
        increment_sent(matrix_context_id)

    return {
        "apollo_lead_id": apollo_lead_id,
        "intento_id": intento_id,
        "email_destino": lead['email'],
        "asunto": asunto_gemini,
        "cuerpo": email_cuerpo,
    }


@router.post("/mark_bounces")
def mark_bounces(req: MarkBouncesRequest):
    """
    Recibe una lista de emails que hicieron bounce según Gmail.
    1. Marca outreach_intentos como bounce_hard.
    2. Para leads Apollo: si tiene email_secundario → crea un nuevo intento REUSANDO el
       asunto/cuerpo del intento original (ya generado por Gemini — no se regenera nada acá,
       la matriz no participa en reintentos). Si no tiene secundario → sin_contacto.
    """
    if not req.email_destinos:
        return {"status": "ok", "marcados": 0}

    marcados = 0
    reintentos_apollo = 0
    ahora = datetime.utcnow()

    for email in req.email_destinos:
        email_clean = email.strip().lower()

        execute(
            """
            UPDATE outreach_intentos
            SET estado = 'bounce_hard', bounce_at = %s
            WHERE email_destino = %s
              AND estado = 'enviado'
              AND bounce_at IS NULL
            """,
            (ahora, email_clean)
        )

        apollo_lead = fetch_one(
            "SELECT id, email_secundario, outreach_intento_id FROM apollo_leads WHERE email = %s AND estado IN ('enviado', 'bounce')",
            (email_clean,)
        )
        if apollo_lead:
            execute(
                "UPDATE apollo_leads SET estado = 'bounce' WHERE id = %s",
                (apollo_lead['id'],)
            )
            if apollo_lead['email_secundario']:
                intento_original = fetch_one(
                    "SELECT tipo, asunto, cuerpo FROM outreach_intentos WHERE id = %s",
                    (apollo_lead['outreach_intento_id'],)
                ) if apollo_lead['outreach_intento_id'] else None

                if intento_original:
                    execute(
                        """
                        INSERT INTO outreach_intentos
                            (lead_id, tipo, email_destino, asunto, cuerpo, estado, programado_para)
                        VALUES (NULL, %s, %s, %s, %s, 'pendiente', %s)
                        """,
                        (intento_original['tipo'], apollo_lead['email_secundario'],
                         intento_original['asunto'], intento_original['cuerpo'], ahora)
                    )
                    execute(
                        "UPDATE apollo_leads SET email = %s, estado = 'pendiente' WHERE id = %s",
                        (apollo_lead['email_secundario'], apollo_lead['id'])
                    )
                    reintentos_apollo += 1
            else:
                execute(
                    "UPDATE apollo_leads SET estado = 'sin_contacto' WHERE id = %s",
                    (apollo_lead['id'],)
                )

        marcados += 1

    return {
        "status": "ok",
        "marcados": marcados,
        "reintentos_apollo": reintentos_apollo,
        "procesados": len(req.email_destinos),
    }
