from fastapi import APIRouter, HTTPException, Query
from datetime import datetime, timedelta
from typing import Optional
from pydantic import BaseModel
import httpx

from database import fetch_one, fetch_all, execute
from services.matrix_selector import increment_sent
from config import get_settings

router = APIRouter()


class MarkSentRequest(BaseModel):
    intento_id: int


class MarkBouncesRequest(BaseModel):
    email_destinos: list[str]  # lista de emails que hicieron bounce según Gmail


class MarkReplyRequest(BaseModel):
    email: str
    asunto: Optional[str] = None
    cuerpo: Optional[str] = None


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

    Acá — y solo acá — es donde apollo_leads.estado pasa a reflejar un envío REAL confirmado
    ('contactado' si era primer_contacto, 'seguimiento_enviado' si era seguimiento). prepare_apollo
    solo deja el lead en 'en_cola' porque en ese momento el mensaje todavía no salió — evita que
    planner_batch lo tome dos veces mientras espera su turno en el Dispatcher.

    Usado por el Flujo B (Dispatcher) después de cada envío exitoso.
    """
    intento = fetch_one(
        "SELECT id, tipo, matrix_id FROM outreach_intentos WHERE id = %s",
        (req.intento_id,)
    )
    if not intento:
        raise HTTPException(status_code=404, detail=f"Intento {req.intento_id} no encontrado")

    ahora = datetime.utcnow()
    execute(
        """
        UPDATE outreach_intentos
        SET estado = 'enviado', enviado_at = %s
        WHERE id = %s
        """,
        (ahora, req.intento_id)
    )

    if intento['matrix_id']:
        increment_sent(intento['matrix_id'])

    apollo_lead = fetch_one(
        "SELECT id FROM apollo_leads WHERE outreach_intento_id = %s",
        (req.intento_id,)
    )
    if apollo_lead:
        if intento['tipo'] == 'seguimiento':
            execute(
                "UPDATE apollo_leads SET estado = 'seguimiento_enviado' WHERE id = %s",
                (apollo_lead['id'],)
            )
        else:
            execute(
                "UPDATE apollo_leads SET estado = 'contactado', contactado_at = %s WHERE id = %s",
                (ahora, apollo_lead['id'])
            )

    return {"status": "ok", "intento_id": req.intento_id}


DAILY_CAP = 15
BUFFER_DIAS_HABILES = 5
MAX_POR_CORRIDA = 30  # tope de leads a generar en una sola corrida del Planner
DIAS_HABILES_ANTES_SEGUIMIENTO = 7  # espera antes de considerar un lead elegible para seguimiento
MAX_SEGUIMIENTOS = 1  # por ahora, uno solo por lead — lo garantiza el filtro por estado

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
def planner_batch(tipo: str = Query(default="primer_contacto", pattern="^(primer_contacto|seguimiento)$")):
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

    tipo=seguimiento: en vez de leads sin contactar, trae leads con estado='contactado' que ya
    pasaron DIAS_HABILES_ANTES_SEGUIMIENTO días hábiles desde contactado_at y todavía no
    respondieron ni recibieron seguimiento. Incluye primer_contacto_asunto/cuerpo en cada lead
    para que Gemini pueda referenciar lo que ya se le escribió, en vez de inventar un mensaje
    sin contexto. La misma lógica de festivos/cupo/buffer aplica para ambos tipos.
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
            WHERE lead_id IS NULL AND tipo = %s
              AND estado IN ('pendiente', 'enviado')
              AND programado_para::date = %s
            """,
            (tipo, candidato)
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
    if tipo == "seguimiento":
        pendientes = fetch_all(
            f"""
            SELECT id, apollo_id, nombre_decisor, cargo, email, empresa, vertical,
                   pais, ciudad, empleados, stack_categoria, tech_stack_apollo,
                   tech_stack_wappalyzer, company_brief, news_snippet, apollo_score_angulo,
                   primer_contacto_asunto, primer_contacto_cuerpo
            FROM apollo_leads
            WHERE estado = 'contactado'
              AND (
                SELECT COUNT(*) FROM generate_series(contactado_at::date + 1, CURRENT_DATE, interval '1 day') d
                WHERE EXTRACT(DOW FROM d) NOT IN (0, 6)
              ) >= {DIAS_HABILES_ANTES_SEGUIMIENTO}
            LIMIT 500
            """,
            ()
        )
    else:
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
        # Festivos futuros del país de ESTE lead — para que n8n pueda evitar proponer un horario
        # de reunión en un día festivo en el país del lead (no solo festivos de Colombia). Ya están
        # calculados arriba para decidir cuándo mandar el email — se reusan, sin llamadas extra.
        lead_dict['festivos_pais'] = sorted(f for f in festivos.get(codigo, set()) if f >= hoy.isoformat())
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

    # El estado esperado del lead depende del tipo: primer_contacto parte de 'pendiente',
    # seguimiento parte de 'contactado' (ya se le mandó el primer_contacto hace rato).
    estado_esperado = 'contactado' if tipo == 'seguimiento' else 'pendiente'
    lead = fetch_one(
        "SELECT * FROM apollo_leads WHERE id = %s AND estado = %s",
        (apollo_lead_id, estado_esperado)
    )
    if not lead:
        raise HTTPException(status_code=404, detail=f"Apollo lead {apollo_lead_id} no encontrado o no está en estado '{estado_esperado}' para tipo={tipo}")

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

    # 'en_cola', no 'enviado' — todavía no lo mandó el Dispatcher. Evita que planner_batch
    # vuelva a tomar este lead mientras espera su turno, sin mentir que ya salió (eso lo
    # confirma /outreach/mark_sent). Si es primer_contacto, guardamos el mensaje para que
    # el futuro seguimiento pueda referenciarlo.
    if tipo == 'primer_contacto':
        execute(
            """
            UPDATE apollo_leads
            SET outreach_intento_id = %s, estado = 'en_cola',
                primer_contacto_asunto = %s, primer_contacto_cuerpo = %s
            WHERE id = %s
            """,
            (intento_id, asunto_gemini, email_cuerpo, apollo_lead_id)
        )
    else:
        execute(
            "UPDATE apollo_leads SET outreach_intento_id = %s, estado = 'en_cola' WHERE id = %s",
            (intento_id, apollo_lead_id)
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
            "SELECT id, email_secundario, outreach_intento_id FROM apollo_leads WHERE email = %s AND estado IN ('en_cola', 'contactado', 'seguimiento_enviado', 'bounce')",
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


def _twenty_mutation(query: str, variables: dict) -> dict:
    settings = get_settings()
    resp = httpx.post(
        f"{settings.twenty_api_url}/graphql",
        headers={"Authorization": f"Bearer {settings.twenty_api_token}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise HTTPException(status_code=502, detail=f"Twenty GraphQL error: {data['errors']}")
    return data["data"]


@router.post("/mark_reply")
def mark_reply(req: MarkReplyRequest):
    """
    Marca un lead Apollo como respondido y crea Company + Person + Opportunity (+ Note si viene
    cuerpo) en Twenty CRM. Único lugar que crea registros en Twenty para leads de cold outreach —
    así Twenty solo recibe leads con interacción real confirmada (filosofía documentada en
    knowledge/comercial/twenty_modelo_datos.md: "no es una base de scraping"), nunca el universo
    bruto de apollo_leads.

    Si el email no matchea ningún lead contactado (en_cola/contactado/seguimiento_enviado),
    devuelve 404 -- así Flujo 3 (Unibox) distingue una respuesta real de cualquier otra cosa que
    le llegue a la bandeja de cold outreach (spam, publicidad, correos de terceros).
    """
    email_clean = req.email.strip().lower()
    lead = fetch_one(
        """
        SELECT id, outreach_intento_id, nombre_decisor, cargo, empresa, dominio, vertical,
               apollo_score, tech_stack_apollo, tech_stack_wappalyzer
        FROM apollo_leads
        WHERE (email = %s OR email_secundario = %s)
          AND estado IN ('en_cola', 'contactado', 'seguimiento_enviado')
        """,
        (email_clean, email_clean)
    )
    if not lead:
        raise HTTPException(status_code=404, detail=f"Ningún lead contactado matchea el email {email_clean}")

    ahora = datetime.utcnow()
    execute("UPDATE apollo_leads SET estado = 'reply' WHERE id = %s", (lead['id'],))
    if lead['outreach_intento_id']:
        execute(
            "UPDATE outreach_intentos SET estado = 'respondio', respondio_at = %s WHERE id = %s",
            (ahora, lead['outreach_intento_id'])
        )

    partes_nombre = (lead['nombre_decisor'] or '').strip().split(' ', 1)
    first_name = partes_nombre[0] if partes_nombre else ''
    last_name = partes_nombre[1] if len(partes_nombre) > 1 else ''

    company_input = {
        "name": lead['empresa'] or 'Lead sin nombre',
        "companyType": "PROSPECT",
        "leadScore": lead['apollo_score'] or 0,
        "techStack": lead['tech_stack_apollo'] or lead['tech_stack_wappalyzer'] or '',
    }
    if lead['vertical']:
        company_input["vertical"] = lead['vertical']
    if lead['dominio']:
        company_input["domainName"] = {"primaryLinkUrl": lead['dominio'], "primaryLinkLabel": ""}

    company = _twenty_mutation(
        "mutation CreateCompany($input: CompanyCreateInput!) { createCompany(data: $input) { id name } }",
        {"input": company_input}
    )["createCompany"]

    person = _twenty_mutation(
        "mutation CreatePerson($input: PersonCreateInput!) { createPerson(data: $input) { id } }",
        {"input": {
            "name": {"firstName": first_name, "lastName": last_name},
            "emails": {"primaryEmail": email_clean},
            "jobTitle": lead['cargo'] or '',
            "companyId": company["id"],
        }}
    )["createPerson"]

    opportunity = _twenty_mutation(
        "mutation CreateOpportunity($input: OpportunityCreateInput!) { createOpportunity(data: $input) { id name stage } }",
        {"input": {
            "name": (lead['empresa'] or 'Lead') + " — Consultoría inicial",
            "stage": "EN_CONTACTO",
            "companyId": company["id"],
            "pointOfContactId": person["id"],
        }}
    )["createOpportunity"]

    if req.cuerpo:
        note = _twenty_mutation(
            "mutation CreateNote($input: NoteCreateInput!) { createNote(data: $input) { id } }",
            {"input": {
                "title": req.asunto or "Respondió al outreach",
                "bodyV2": {"markdown": req.cuerpo},
            }}
        )["createNote"]
        _twenty_mutation(
            "mutation CreateNoteTarget($input: NoteTargetCreateInput!) { createNoteTarget(data: $input) { id } }",
            {"input": {"noteId": note["id"], "targetOpportunityId": opportunity["id"]}}
        )

    return {
        "status": "ok",
        "apollo_lead_id": lead['id'],
        "empresa": lead['empresa'],
        "twenty": {
            "company_id": company["id"],
            "person_id": person["id"],
            "opportunity_id": opportunity["id"],
        },
    }
