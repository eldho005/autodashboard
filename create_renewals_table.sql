-- Renewals Pipeline Table
-- Run this in Supabase SQL Editor to create the table
--
-- Tracks leads with status "Quote Sent" that have a future renewal date.
-- Separate from leads table for reliability — no schema dependency.

CREATE TABLE IF NOT EXISTS renewals (
    id              BIGSERIAL PRIMARY KEY,
    lead_id         UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    renewal_date    TEXT NOT NULL,                              -- Stored as YYYY-MM-DD
    quoted_premium  NUMERIC(12,2) NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',             -- 'active' or 'reopened'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookups
CREATE INDEX IF NOT EXISTS idx_renewals_lead_id ON renewals(lead_id);
CREATE INDEX IF NOT EXISTS idx_renewals_status  ON renewals(status);
CREATE INDEX IF NOT EXISTS idx_renewals_date    ON renewals(renewal_date);

-- Only one active renewal per lead (prevents duplicates)
CREATE UNIQUE INDEX IF NOT EXISTS idx_renewals_active_lead
    ON renewals(lead_id) WHERE status = 'active';
