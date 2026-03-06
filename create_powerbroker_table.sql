-- PowerBroker Data Table
-- Run this in Supabase SQL Editor to create the table

CREATE TABLE IF NOT EXISTS powerbroker_data (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT,
    lead_id     UUID REFERENCES leads(id),
    powerbroker_data JSONB,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for fast lookup
CREATE INDEX IF NOT EXISTS idx_powerbroker_email   ON powerbroker_data(email);
CREATE INDEX IF NOT EXISTS idx_powerbroker_lead_id ON powerbroker_data(lead_id);
