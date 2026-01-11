#!/bin/bash
# Setup script for initializing all databases

set -e

echo "Setting up Linguistic Stratigraphy databases..."

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi

# Start services with docker-compose
echo "Starting database services..."
docker-compose up -d neo4j postgres elasticsearch redis milvus

# Wait for services to be ready
echo "Waiting for services to initialize..."
sleep 30

# Initialize PostgreSQL schema
echo "Initializing PostgreSQL schema..."
docker-compose exec -T postgres psql -U ls_user -d linguistic_stratigraphy << 'EOF'
-- Core tables for relational queries and metadata

CREATE TABLE IF NOT EXISTS languages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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

CREATE TABLE IF NOT EXISTS corpora (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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

CREATE TABLE IF NOT EXISTS validation_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lsr_id UUID NOT NULL,
    validation_type VARCHAR(100),
    priority INTEGER DEFAULT 5,
    assigned_to VARCHAR(255),
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS contact_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
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

-- Indexes
CREATE INDEX IF NOT EXISTS idx_languages_family ON languages(family);
CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_jobs(status);
CREATE INDEX IF NOT EXISTS idx_validation_priority ON validation_queue(priority DESC, created_at ASC);

EOF

# Initialize Neo4j indexes
echo "Initializing Neo4j indexes..."
docker-compose exec -T neo4j cypher-shell -u neo4j -p password << 'EOF'
CREATE INDEX lsr_form IF NOT EXISTS FOR (n:LSR) ON (n.form_normalized);
CREATE INDEX lsr_language IF NOT EXISTS FOR (n:LSR) ON (n.language_code);
CREATE INDEX lsr_date IF NOT EXISTS FOR (n:LSR) ON (n.date_start);
CREATE INDEX language_code IF NOT EXISTS FOR (n:Language) ON (n.iso_code);
EOF

echo "Database setup complete!"
