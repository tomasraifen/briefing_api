-- Apollo leads enrichment fields — ejecutado 2026-07-04
-- Parte del rediseño del pipeline cold outreach (prompt maestro Gemini)

ALTER TABLE apollo_leads ADD COLUMN IF NOT EXISTS apollo_score INTEGER DEFAULT 0;
ALTER TABLE apollo_leads ADD COLUMN IF NOT EXISTS apollo_score_angulo TEXT;
ALTER TABLE apollo_leads ADD COLUMN IF NOT EXISTS company_brief TEXT;
ALTER TABLE apollo_leads ADD COLUMN IF NOT EXISTS news_snippet TEXT;
ALTER TABLE apollo_leads ADD COLUMN IF NOT EXISTS enriquecido_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_apollo_score ON apollo_leads(apollo_score DESC);
CREATE INDEX IF NOT EXISTS idx_apollo_enriquecido ON apollo_leads(enriquecido_at) WHERE enriquecido_at IS NULL;
