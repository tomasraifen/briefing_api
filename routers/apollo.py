from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime
import re
import httpx

from database import fetch_one, fetch_all, execute

router = APIRouter()

# Mapa código ISO → nombre en apollo_leads (campo pais)
_PAIS_MAP = {
    'CO': 'Colombia', 'MX': 'México', 'CL': 'Chile', 'AR': 'Argentina',
    'PE': 'Perú', 'PA': 'Panamá', 'GT': 'Guatemala', 'SV': 'El Salvador',
    'NI': 'Nicaragua',
}

_TECH_SIGNALS = [
    (re.compile(r'shopify', re.I), 'Shopify'),
    (re.compile(r'woocommerce', re.I), 'WooCommerce'),
    (re.compile(r'wp-content|wordpress', re.I), 'WordPress'),
    (re.compile(r'magento', re.I), 'Magento'),
    (re.compile(r'vtex', re.I), 'VTEX'),
    (re.compile(r'hubspot', re.I), 'HubSpot'),
    (re.compile(r'salesforce', re.I), 'Salesforce'),
    (re.compile(r'google-analytics|gtag\(', re.I), 'Google Analytics'),
    (re.compile(r'intercom', re.I), 'Intercom'),
    (re.compile(r'zendesk', re.I), 'Zendesk'),
    (re.compile(r'sap\.', re.I), 'SAP'),
    (re.compile(r'oracle', re.I), 'Oracle'),
    (re.compile(r'siigo', re.I), 'Siigo'),
    (re.compile(r'react', re.I), 'React'),
    (re.compile(r'next\.js|nextjs', re.I), 'Next.js'),
    (re.compile(r'aws\.amazon|cloudfront', re.I), 'AWS'),
    (re.compile(r'stripe', re.I), 'Stripe'),
    (re.compile(r'mercadopago', re.I), 'MercadoPago'),
]


def _detect_tech_stack_simple(domain: str) -> Optional[str]:
    """Scraping básico para detectar stack tecnológico de un dominio."""
    detected = []
    for scheme in ('https', 'http'):
        try:
            r = httpx.get(f"{scheme}://{domain}", timeout=8.0, follow_redirects=True,
                          headers={'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1)'})
            if r.status_code == 200:
                html = r.text
                for pattern, name in _TECH_SIGNALS:
                    if pattern.search(html) and name not in detected:
                        detected.append(name)
                break
        except Exception:
            continue
    return ', '.join(detected) if detected else None


def _calc_score(tech_all: str, cargo: str, vertical: str, empleados: Optional[int], has_brief: bool):
    """Calcula apollo_score (0-100) y angulo de pitch."""
    t = tech_all.lower()
    c = (cargo or '').lower()
    v = (vertical or '').lower()
    score = 0
    angulo = 'ops_first'

    if any(x in t for x in ['artificial intelligence', ' ai,', ',ai,', ' ai ', 'machine learning', 'generative']):
        score += 35
    if 'hubspot' in t:
        score += 25
    if 'salesforce' in t:
        score += 30
    if 'sap' in t or 'oracle' in t:
        score += 25
    if any(x in t for x in ['databricks', 'snowflake', 'bigquery', 'redshift']):
        score += 40
    if 'aws' in t:
        score += 10
    if 'slack' in t:
        score += 10
    if any(x in t for x in ['tableau', 'power bi', 'metabase', 'looker']):
        score += 15
        angulo = 'ia_first'
    if has_brief:
        score += 10

    if any(x in c for x in ['ceo', 'cto', 'director', 'gerente general', 'founder', 'vp ', 'chief']):
        score += 20

    if empleados and int(empleados) >= 100:
        score += 20
    elif empleados and int(empleados) >= 50:
        score += 10

    if any(v_kw in v for v_kw in ['tecnolog', 'software', 'information technology', 'consultor', 'saas']):
        angulo = 'ia_first'

    return min(score, 100), angulo


class ApolloLeadIn(BaseModel):
    apollo_id: str
    nombre_decisor: Optional[str] = None
    cargo: Optional[str] = None
    email: str
    linkedin_url: Optional[str] = None
    empresa: Optional[str] = None
    dominio: Optional[str] = None
    vertical: Optional[str] = None
    pais: Optional[str] = 'Colombia'
    ciudad: Optional[str] = None
    empleados: Optional[int] = None
    tech_stack_apollo: Optional[str] = None
    tech_stack_wappalyzer: Optional[str] = None
    stack_categoria: Optional[str] = None
    mensaje_intro: Optional[str] = None


@router.get("/check")
def check_apollo_lead(apollo_id: str = Query(...)):
    """
    Verifica si un apollo_id ya existe en la tabla.
    Usado por Flujo 5 para evitar duplicados.
    """
    row = fetch_one("SELECT id FROM apollo_leads WHERE apollo_id = %s", (apollo_id,))
    return {"exists": row is not None}


@router.post("/", status_code=201)
def create_apollo_lead(lead: ApolloLeadIn):
    """
    Guarda un nuevo lead de Apollo. Ignora duplicados (ON CONFLICT DO NOTHING).
    El tech stack se enriquece después, vía POST /apollo-leads/enrich-batch.
    """
    tech_wap = lead.tech_stack_wappalyzer
    stack_cat = lead.stack_categoria

    result = execute(
        """
        INSERT INTO apollo_leads
            (apollo_id, nombre_decisor, cargo, email, linkedin_url, empresa, dominio,
             vertical, pais, ciudad, empleados, tech_stack_apollo, tech_stack_wappalyzer,
             stack_categoria, mensaje_intro)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (apollo_id) DO NOTHING
        RETURNING id
        """,
        (lead.apollo_id, lead.nombre_decisor, lead.cargo, lead.email, lead.linkedin_url,
         lead.empresa, lead.dominio, lead.vertical, lead.pais, lead.ciudad, lead.empleados,
         lead.tech_stack_apollo, tech_wap, stack_cat, lead.mensaje_intro)
    )
    if not result:
        return {"status": "duplicate", "apollo_id": lead.apollo_id}
    return {"status": "created", "id": result['id'], "apollo_id": lead.apollo_id}


@router.get("/pending")
def get_pending_apollo(
    limit: int = Query(default=10, ge=1, le=50),
    paises: Optional[str] = Query(default=None, description="Códigos ISO separados por coma: CO,MX,CL"),
):
    """
    Devuelve leads de Apollo listos para enviar.
    - estado = 'pendiente'
    - Filtra por paises (códigos ISO) si se indica
    Incluye company_brief, news_snippet y apollo_score_angulo para el prompt Gemini.
    Usado por Flujo A.
    """
    pais_filter = ""
    params: list = []

    if paises:
        codigos = [p.strip().upper() for p in paises.split(',') if p.strip()]
        nombres = [_PAIS_MAP[c] for c in codigos if c in _PAIS_MAP]
        if nombres:
            placeholders = ','.join(['%s'] * len(nombres))
            pais_filter = f" AND pais IN ({placeholders})"
            params = nombres

    rows = fetch_all(
        f"""
        SELECT id, apollo_id, nombre_decisor, cargo, email, empresa,
               dominio, vertical, pais, ciudad, empleados,
               stack_categoria, tech_stack_apollo, tech_stack_wappalyzer,
               company_brief, news_snippet, apollo_score, apollo_score_angulo
        FROM apollo_leads
        WHERE estado = 'pendiente'
          {pais_filter}
        ORDER BY apollo_score DESC NULLS LAST, importado_at ASC
        LIMIT %s
        """,
        tuple(params) + (limit,)
    )
    return {
        "leads": [dict(r) for r in rows] if rows else [],
        "total": len(rows) if rows else 0,
    }


@router.post("/enrich-batch")
def enrich_apollo_batch(batch_size: int = Query(default=5, ge=1, le=20)):
    """
    Enriquece un lote de apollo_leads sin enriquecer (enriquecido_at IS NULL):
    1. Tech stack por scraping HTML directo del dominio
    2. company_brief via Jina Reader (r.jina.ai)
    3. news_snippet via Google News RSS
    4. Calcula apollo_score + apollo_score_angulo
    Llamado por Flujo 2 Rama B cada 5 min.
    """
    leads = fetch_all(
        """
        SELECT id, dominio, empresa, vertical, cargo, empleados,
               tech_stack_apollo, tech_stack_wappalyzer, pais
        FROM apollo_leads
        WHERE enriquecido_at IS NULL
          AND dominio IS NOT NULL AND dominio != ''
        ORDER BY importado_at ASC
        LIMIT %s
        """,
        (batch_size,)
    )

    if not leads:
        return {"processed": 0, "enriched": 0, "message": "Sin leads pendientes de enriquecimiento"}

    enriched = 0
    for lead in leads:
        lead_id = lead['id']
        dominio = (lead['dominio'] or '').strip().lower()
        empresa = lead['empresa'] or ''

        # 1. Tech stack: scraping directo del dominio
        tech_wap = lead['tech_stack_wappalyzer']
        stack_cat = None
        if not tech_wap:
            tech_wap = _detect_tech_stack_simple(dominio)
            if tech_wap:
                ts = tech_wap.lower()
                if any(x in ts for x in ['shopify', 'woocommerce', 'vtex', 'magento']):
                    stack_cat = 'ecommerce'
                elif any(x in ts for x in ['sap', 'oracle', 'siigo']):
                    stack_cat = 'erp'
                else:
                    stack_cat = 'basico'

        # 2. company_brief via Jina Reader
        company_brief = None
        try:
            r = httpx.get(f"https://r.jina.ai/{dominio}", timeout=12.0, follow_redirects=True,
                          headers={'Accept': 'text/plain', 'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200 and len(r.text) > 50:
                company_brief = r.text[:1200].strip()
        except Exception:
            pass

        # 3. news_snippet via Google News RSS
        news_snippet = None
        try:
            q = empresa.replace(' ', '+')
            rss_url = f"https://news.google.com/rss/search?q={q}&hl=es&gl=CO&ceid=CO:es"
            r = httpx.get(rss_url, timeout=8.0, follow_redirects=True)
            if r.status_code == 200:
                item_titles = re.findall(r'<item>.*?<title>(.*?)</title>', r.text, re.DOTALL)
                if item_titles:
                    news_snippet = item_titles[0][:300]
        except Exception:
            pass

        # 4. Calcular score
        tech_all = ' '.join(filter(None, [lead['tech_stack_apollo'], tech_wap]))
        score, angulo = _calc_score(
            tech_all=tech_all,
            cargo=lead['cargo'],
            vertical=lead['vertical'],
            empleados=lead['empleados'],
            has_brief=bool(company_brief),
        )

        execute(
            """
            UPDATE apollo_leads SET
                tech_stack_wappalyzer = COALESCE(tech_stack_wappalyzer, %s),
                stack_categoria = COALESCE(stack_categoria, %s),
                company_brief = %s,
                news_snippet = %s,
                apollo_score = %s,
                apollo_score_angulo = %s,
                enriquecido_at = NOW()
            WHERE id = %s
            """,
            (tech_wap, stack_cat, company_brief, news_snippet, score, angulo, lead_id)
        )
        enriched += 1

    return {"processed": len(leads), "enriched": enriched}


@router.patch("/{lead_id}/estado")
def update_apollo_estado(lead_id: int, estado: str, intento_id: Optional[int] = None):
    """
    Actualiza el estado de un lead Apollo manualmente (uso administrativo/debug — el flujo normal
    lo maneja prepare_apollo/mark_sent/mark_bounces).
    Estados válidos: pendiente | en_cola | contactado | seguimiento_enviado | bounce | reply | descartado
    """
    estados_validos = {
        'pendiente', 'en_cola', 'contactado', 'seguimiento_enviado',
        'bounce', 'reply', 'descartado'
    }
    if estado not in estados_validos:
        raise HTTPException(status_code=400, detail=f"Estado inválido. Válidos: {estados_validos}")

    execute(
        """
        UPDATE apollo_leads
        SET estado = %s,
            contactado_at = CASE WHEN %s = 'contactado' THEN NOW() ELSE contactado_at END,
            outreach_intento_id = COALESCE(%s, outreach_intento_id)
        WHERE id = %s
        """,
        (estado, estado, intento_id, lead_id)
    )
    return {"status": "ok", "lead_id": lead_id, "estado": estado}


@router.get("/stats")
def apollo_stats():
    """Resumen del estado actual de la tabla apollo_leads."""
    row = fetch_one(
        """
        SELECT
            COUNT(*) FILTER (WHERE estado = 'pendiente') AS pendientes,
            COUNT(*) FILTER (WHERE estado = 'en_cola') AS en_cola,
            COUNT(*) FILTER (WHERE estado = 'contactado') AS contactados,
            COUNT(*) FILTER (WHERE estado = 'seguimiento_enviado') AS seguimientos_enviados,
            COUNT(*) FILTER (WHERE estado = 'bounce') AS bounces,
            COUNT(*) FILTER (WHERE estado = 'reply') AS replies,
            COUNT(*) FILTER (WHERE estado = 'descartado') AS descartados,
            COUNT(*) FILTER (WHERE mensaje_intro IS NULL) AS sin_intro,
            COUNT(*) AS total
        FROM apollo_leads
        """, ()
    )
    return dict(row) if row else {}
