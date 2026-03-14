-- ============================================================
-- Document Verification Table
-- Run this in Supabase SQL Editor
-- ============================================================

CREATE TABLE IF NOT EXISTS doc_verifications (
    id              BIGSERIAL PRIMARY KEY,
    client_name     TEXT,
    verified_by     TEXT,
    lead_id         BIGINT REFERENCES leads(id) ON DELETE SET NULL,
    quote_number    TEXT,

    -- Raw extracted data per document (JSON)
    quote_data          JSONB,
    dash_data           JSONB,
    mvr_data            JSONB,
    application_data    JSONB,

    -- Cross-comparison result computed server-side
    comparison_result   JSONB,

    -- Errors encountered during Vertex AI extraction
    extraction_errors   JSONB,

    -- Verification status: pending | approved | cannot_bind
    status          TEXT NOT NULL DEFAULT 'pending',

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_doc_verifications_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_doc_verifications_updated_at ON doc_verifications;
CREATE TRIGGER trg_doc_verifications_updated_at
    BEFORE UPDATE ON doc_verifications
    FOR EACH ROW EXECUTE FUNCTION update_doc_verifications_updated_at();

-- Indexes
CREATE INDEX IF NOT EXISTS idx_doc_verifications_lead_id    ON doc_verifications(lead_id);
CREATE INDEX IF NOT EXISTS idx_doc_verifications_created_at ON doc_verifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_verifications_status     ON doc_verifications(status);
