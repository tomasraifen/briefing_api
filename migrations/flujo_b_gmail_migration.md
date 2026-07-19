# Flujo B — Migración Instantly → Gmail API

**Fecha:** 2026-06-30  
**Ejecuta:** Tom en n8n UI  
**Tiempo estimado:** 15 min

---

## Cambios a hacer en n8n

### 1. Abrir Flujo B (Dispatcher de Outreach)

En n8n → Workflows → buscar "Flujo B" o "Dispatcher".

---

### 2. Eliminar nodo `send_instantly`

El nodo actual envía a:
```
POST https://api.instantly.ai/api/v2/leads
Body: { email, firstName, campaign_id, subject, body }
```

Eliminarlo.

---

### 3. Agregar nodo `Wait` (delay anti-spam)

**Tipo:** Wait  
**Configuración:**
- Resume: After time interval
- Amount: Expression → `{{ Math.floor(Math.random() * 120) + 60 }}` (60–180 segundos)
- Unit: Seconds

Conectar: `get_pending` → `split` (si existe) → **`wait_delay`** → nuevo nodo Gmail

---

### 4. Agregar nodo `send_gmail` (Gmail API)

**Tipo:** Gmail  
**Credencial:** `Gmail OAuth - Cold Outreach .Cloud` (ya configurada)  
**Operación:** Send

**Campos:**
| Campo | Valor |
|---|---|
| To | `{{ $json.email_destino }}` |
| Subject | `{{ $json.asunto }}` |
| Message | `{{ $json.cuerpo }}` |
| From Name | `Tomás Perea · Sediment Data` |

> El from address lo determina la credencial OAuth (tomas@sediment.cloud).

---

### 5. Actualizar nodo `mark_sent`

El nodo que llama `POST /outreach/mark_sent` actualmente manda `instantly_id`.  
Con Gmail API ya no hay ID de Instantly. Actualizar el body:

```json
{
  "intento_id": "{{ $json.intento_id }}",
  "instantly_id": ""
}
```

O simplemente dejar `instantly_id` vacío — el endpoint ya acepta vacío.

---

### 6. Verificar el flujo completo

Orden de nodos después de la migración:
```
cron_30min
  → get_pending     [HTTP GET] /outreach/pending
  → split           [SplitInBatches] de a 1
  → wait_delay      [Wait] 60-180 seg aleatorios
  → send_gmail      [Gmail OAuth] send email
  → mark_sent       [HTTP POST] /outreach/mark_sent
  → error_handler   (si existe)
```

---

### 7. Test con Pin Data

Antes de activar en producción:

1. Desactivar el cron trigger (modo manual)
2. Hacer click en `get_pending` → "Execute Node"
3. Verificar que devuelve intentos
4. Usar Pin Data en `get_pending` con 1 intento de prueba
5. Ejecutar el flujo completo
6. Verificar que llega email a `tomas@sedimentdata.com` desde `tomas@sediment.cloud`
7. Si pasa → activar cron

---

### 8. Post-verificación: cancelar Instantly

Solo después de confirmar que Gmail funciona:
1. Ir a Instantly.ai → Account → Subscription → Cancel
2. En Coolify → n8n → Variables de entorno → eliminar:
   - `INSTANTLY_API_KEY`
   - `INSTANTLY_CAMPAIGN_PRIMER_CONTACTO`
   - `INSTANTLY_CAMPAIGN_SEGUIMIENTO`
3. Redeploy del servicio n8n en Coolify
