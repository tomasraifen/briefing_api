# briefing_api

API FastAPI de outreach de Raifen — pipeline Apollo + Gemini (puerto 8002).

## Qué hace

- Recibe leads de Apollo.io (vía n8n) y los guarda en `apollo_leads`
- Enriquece tech stack, company brief y news snippet por lead
- Arma el batch diario del Planner (`/outreach/planner_batch`) respetando festivos por país y un colchón de días hábiles — el Planner programa a futuro, nunca "hoy", así si un día falla el siguiente compensa
- Registra los intentos de outreach (`outreach_intentos`) con el asunto y el cuerpo que genera Gemini (n8n) — `message_matrix` ya no es el mensaje final, es un banco de contexto/tono por vertical que n8n consulta antes de armar el prompt (`GET /matrix/context/{tipo}/{vertical}`)
- Maneja bounces con reintento automático a `email_secundario`

## Setup local

```bash
py -3.12 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# completar .env con credenciales reales — nunca subir .env al repo
uvicorn main:app --reload --port 8002
```

Docs disponibles en desarrollo: http://localhost:8002/docs

## Deploy en Coolify

Deploy directo desde `Dockerfile` (no hay `docker-compose.yml`, un solo servicio). Variables de entorno requeridas en `.env.example`.
