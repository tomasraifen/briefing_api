# Setup de desarrollo local — briefing_api

## Primera vez (solo se hace una vez)

### 1. Instalar Python 3.12
Descargar de: https://www.python.org/downloads/release/python-31210/
Marcar "Add Python to PATH" durante la instalación.

### 2. Crear entorno virtual
```powershell
cd "D:\Dokumente\Laboral\Raifen\1. Github Repositories\1. Raifen Github Repos\briefing_api"
py -3.12 -m venv venv
```

### 3. Instalar dependencias
```powershell
venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configurar variables de entorno
```powershell
copy .env.example .env
notepad .env
```
Completar `.env` con:
```
ENVIRONMENT=development
API_PORT=8002
DATABASE_URL=postgresql://usuario:password@host:5432/nombre_db
API_KEY=raifen-dev-2026
DEV_EMAIL_OVERRIDE=tu-correo@raifen.ai   ← tu correo real
TWENTY_API_URL=https://twenty.raifen.ai
TWENTY_API_TOKEN=                        ← dejar vacío en dev
```

---

## Cada vez que querés desarrollar o testear

```powershell
cd "D:\Dokumente\Laboral\Raifen\1. Github Repositories\1. Raifen Github Repos\briefing_api"
venv\Scripts\activate
uvicorn main:app --reload --port 8002
```

Swagger UI disponible en: **http://localhost:8002/docs**

Para detener: **Ctrl+C** en la terminal.

---

## Garantías de seguridad en modo development

| Riesgo | Protección |
|---|---|
| Escribir intentos reales en la BD | Los endpoints de outreach solo escriben `outreach_intentos`/`apollo_leads` cuando se los llama explícitamente — no hay dry-run automático, probar con leads dummy (ver abajo) |
| Enviar email a un prospecto real | `DEV_EMAIL_OVERRIDE` — si se usa como destino en pruebas manuales, redirigir ahí antes de que n8n dispare el envío real por Gmail |
| Subir credenciales al repo | `.env` está en `.gitignore`, nunca se sube |

## Leads de prueba en la BD

`leads_brutos` ya no existe — toda la fuente de leads es `apollo_leads`. Para testear sin depender de leads reales:

```sql
-- Leads dummy para desarrollo — no tocar en producción
INSERT INTO apollo_leads (
    apollo_id, nombre_decisor, cargo, email, empresa, dominio,
    vertical, pais, ciudad, empleados, stack_categoria, estado
) VALUES
('dev_test_1', 'Ana Demo', 'Gerente General', 'contacto@clinica-demo.co', 'Clínica Demo Salud', 'clinica-demo.co',
 'salud', 'Colombia', 'Bogotá', 80, 'ecommerce', 'pendiente'),
('dev_test_2', 'Luis Demo', 'Director de Operaciones', 'ventas@manufactura-demo.com', 'Manufactura Demo SAS', 'manufactura-demo.com',
 'manufactura', 'Colombia', 'Medellín', 120, 'erp', 'pendiente');

-- Verificar
SELECT id, empresa, vertical, stack_categoria, estado FROM apollo_leads WHERE apollo_id LIKE 'dev_test_%';
```

Para limpiar los leads de prueba después:
```sql
DELETE FROM apollo_leads WHERE apollo_id LIKE 'dev_test_%';
```

---

## Subir cambios a GitHub

```powershell
git add .
git commit -m "descripción del cambio"
git push
```

Coolify hace redeploy automático al detectar el push (si está configurado el webhook de GitHub).

---

## Variables de entorno en Coolify (producción)

Ir a Coolify → Servicio `briefing_api` → Environment Variables y verificar:

| Variable | Valor |
|---|---|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | URL real de PostgreSQL (leads DB de Raifen) |
| `API_KEY` | Clave compartida con n8n |
| `DEV_EMAIL_OVERRIDE` | *(vacío)* |
| `TWENTY_API_URL` | `https://twenty.raifen.ai` |
| `TWENTY_API_TOKEN` | Token generado en Twenty → Settings → API |
