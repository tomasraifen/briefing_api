-- Reset message_matrix — todos los conteos son de pruebas o falsos positivos
-- Fecha: 2026-06-30
-- Ejecutar una sola vez antes de activar Apollo + Gmail API en producción

-- Ver estado antes
SELECT id, vertical, stack_categoria, tipo, emails_enviados, emails_respondidos, tasa_respuesta
FROM message_matrix
ORDER BY id;

-- Reset de contadores
UPDATE message_matrix
SET emails_respondidos = 0,
    tasa_respuesta = 0.0;

-- Opcional: también resetear emails_enviados si queremos empezar con métricas limpias
-- UPDATE message_matrix SET emails_enviados = 0;

-- Verificar
SELECT COUNT(*) AS filas_reseteadas
FROM message_matrix
WHERE emails_respondidos = 0 AND tasa_respuesta = 0.0;
