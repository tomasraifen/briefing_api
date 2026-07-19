-- Migración: limpiar emails con extensiones de imagen en leads_brutos.emails_sitio
-- Fecha: 2026-06-30
-- Afecta: ~1,041 leads según auditoría
-- Ejecutar una sola vez. Es idempotente.

-- 1. Ver magnitud del problema antes de limpiar
SELECT COUNT(*) AS afectados
FROM leads_brutos
WHERE emails_sitio ~ '\.(png|jpg|jpeg|webp|svg|gif|ico|avif|bmp|pdf)';

-- 2. Función temporal para filtrar emails de imagen dentro del campo semicolon-delimitado
-- Lógica: split por ';', descartar los que contengan extensión de imagen, rejoin
-- Si queda vacío → NULL

UPDATE leads_brutos
SET emails_sitio = (
    SELECT CASE
        WHEN array_length(
            ARRAY(
                SELECT trim(e)
                FROM unnest(string_to_array(emails_sitio, ';')) AS e
                WHERE trim(e) !~ '\.(png|jpg|jpeg|webp|svg|gif|ico|avif|bmp|pdf)$'
                  AND trim(e) !~ '^\.'
                  AND length(trim(e)) > 0
            ), 1
        ) > 0
        THEN array_to_string(
            ARRAY(
                SELECT trim(e)
                FROM unnest(string_to_array(emails_sitio, ';')) AS e
                WHERE trim(e) !~ '\.(png|jpg|jpeg|webp|svg|gif|ico|avif|bmp|pdf)$'
                  AND trim(e) !~ '^\.'
                  AND length(trim(e)) > 0
            ), ';'
        )
        ELSE NULL
    END
)
WHERE emails_sitio ~ '\.(png|jpg|jpeg|webp|svg|gif|ico|avif|bmp|pdf)';

-- 3. Verificar resultado
SELECT COUNT(*) AS quedan_sucios
FROM leads_brutos
WHERE emails_sitio ~ '\.(png|jpg|jpeg|webp|svg|gif|ico|avif|bmp|pdf)';
-- Debe devolver 0
