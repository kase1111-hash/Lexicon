-- Linguistic Stratigraphy - Database Initialization
-- This script runs automatically when the PostgreSQL container starts

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- =============================================================================
-- Core Tables (from SPEC.md Section 2.2)
-- =============================================================================

-- Languages table
CREATE TABLE IF NOT EXISTS languages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    iso_code VARCHAR(3) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    family VARCHAR(255),
    branch_path TEXT[],
    is_living BOOLEAN DEFAULT true,
    is_reconstructed BOOLEAN DEFAULT false,
    speaker_count INTEGER,
    geographic_region TEXT[],
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Corpora table
CREATE TABLE IF NOT EXISTS corpora (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    language_codes TEXT[],
    date_range_start INTEGER,
    date_range_end INTEGER,
    source_url TEXT,
    license VARCHAR(100),
    ingestion_status VARCHAR(50) DEFAULT 'pending',
    last_ingested TIMESTAMP,
    record_count INTEGER DEFAULT 0
);

-- Ingestion jobs table
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type VARCHAR(100) NOT NULL,
    source_identifier TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'queued',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    records_processed INTEGER DEFAULT 0,
    records_created INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    records_failed INTEGER DEFAULT 0,
    error_log TEXT,
    config JSONB
);

-- Validation queue table
CREATE TABLE IF NOT EXISTS validation_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lsr_id UUID NOT NULL,
    validation_type VARCHAR(100),
    priority INTEGER DEFAULT 5,
    assigned_to VARCHAR(255),
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

-- Contact events table
CREATE TABLE IF NOT EXISTS contact_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    donor_language UUID REFERENCES languages(id),
    recipient_language UUID REFERENCES languages(id),
    date_start INTEGER,
    date_end INTEGER,
    contact_type VARCHAR(100),
    vocabulary_count INTEGER,
    confidence FLOAT,
    detected_by VARCHAR(50),
    validated BOOLEAN DEFAULT false,
    notes TEXT
);

-- =============================================================================
-- Indexes
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_languages_family ON languages(family);
CREATE INDEX IF NOT EXISTS idx_languages_iso_code ON languages(iso_code);
CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_source ON ingestion_jobs(source_type, source_identifier);
CREATE INDEX IF NOT EXISTS idx_validation_priority ON validation_queue(priority DESC, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_validation_status ON validation_queue(status);
CREATE INDEX IF NOT EXISTS idx_contact_languages ON contact_events(donor_language, recipient_language);

-- =============================================================================
-- Triggers for updated_at
-- =============================================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_languages_updated_at
    BEFORE UPDATE ON languages
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- Initial Data
-- =============================================================================

-- Insert some common language families for reference
INSERT INTO languages (iso_code, name, family, is_living, is_reconstructed)
VALUES
    ('eng', 'English', 'Indo-European', true, false),
    ('deu', 'German', 'Indo-European', true, false),
    ('fra', 'French', 'Indo-European', true, false),
    ('lat', 'Latin', 'Indo-European', false, false),
    ('grc', 'Ancient Greek', 'Indo-European', false, false),
    ('ang', 'Old English', 'Indo-European', false, false),
    ('gem-pro', 'Proto-Germanic', 'Indo-European', false, true),
    ('ine-pro', 'Proto-Indo-European', 'Indo-European', false, true)
ON CONFLICT (iso_code) DO NOTHING;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO ls_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO ls_user;
