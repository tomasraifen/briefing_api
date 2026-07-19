from fastapi import FastAPI, Depends
from routers import health, matrix, outreach, webhooks, leads, analytics, apollo
from config import get_settings
from auth import verify_api_key

settings = get_settings()

app = FastAPI(
    title="Raifen Briefing API",
    description="API de outreach de Raifen — pipeline Apollo + Gemini, banco de contexto por vertical y webhooks",
    version="2.0.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
)

# /health queda sin auth (lo pega Coolify para el healthcheck del contenedor).
# Todo lo demás exige el header X-API-Key.
app.include_router(health.router)
app.include_router(matrix.router,    prefix="/matrix",    tags=["Matrix"],    dependencies=[Depends(verify_api_key)])
app.include_router(outreach.router,  prefix="/outreach",  tags=["Outreach"],  dependencies=[Depends(verify_api_key)])
app.include_router(webhooks.router,  prefix="/webhooks",  tags=["Webhooks"],  dependencies=[Depends(verify_api_key)])
app.include_router(leads.router,     prefix="/leads",     tags=["Leads"],     dependencies=[Depends(verify_api_key)])
app.include_router(analytics.router, prefix="/analytics", tags=["Analytics"], dependencies=[Depends(verify_api_key)])
app.include_router(apollo.router,    prefix="/apollo-leads", tags=["Apollo Leads"], dependencies=[Depends(verify_api_key)])
# /twenty/create_lead_record se retiró: dependía de leads_brutos (borrada) y de un stage
# ("RESPONDIO") que no existe en el modelo de 5 stages actual de Twenty. Nada lo llama hoy
# (el webhook de Instantly que lo disparaba está desactivado; Outreach C — Unibox hace GraphQL
# directo, sin pasar por acá). Reconstruir cuando se rehaga Unibox, contra apollo_leads y el
# stage real de Twenty (ver knowledge/comercial/twenty_modelo_datos.md).
