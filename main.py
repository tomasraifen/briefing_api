from fastapi import FastAPI
from routers import health, matrix, outreach, webhooks, leads, analytics, apollo
from config import get_settings

settings = get_settings()

app = FastAPI(
    title="Raifen Briefing API",
    description="API de outreach de Raifen — pipeline Apollo + Gemini, banco de contexto por vertical y webhooks",
    version="2.0.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
)

app.include_router(health.router)
app.include_router(matrix.router,    prefix="/matrix",    tags=["Matrix"])
app.include_router(outreach.router,  prefix="/outreach",  tags=["Outreach"])
app.include_router(webhooks.router,  prefix="/webhooks",  tags=["Webhooks"])
app.include_router(leads.router,     prefix="/leads",     tags=["Leads"])
app.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
app.include_router(apollo.router,    prefix="/apollo-leads", tags=["Apollo Leads"])
# /twenty/create_lead_record se retiró: dependía de leads_brutos (borrada) y de un stage
# ("RESPONDIO") que no existe en el modelo de 5 stages actual de Twenty. Nada lo llama hoy
# (el webhook de Instantly que lo disparaba está desactivado; Outreach C — Unibox hace GraphQL
# directo, sin pasar por acá). Reconstruir cuando se rehaga Unibox, contra apollo_leads y el
# stage real de Twenty (ver knowledge/comercial/twenty_modelo_datos.md).
