"""SQLAlchemy models for PostgreSQL database.

These models define the relational schema for metadata storage,
complementing the Neo4j graph database for LSR relationships.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class Language(Base):
    """Language metadata table."""

    __tablename__ = "languages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    code = Column(String(10), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    family = Column(String(100))
    branch = Column(ARRAY(String))
    status = Column(String(50), default="living")  # living, extinct, reconstructed
    speaker_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    lsrs = relationship("LSRMetadata", back_populates="language")

    __table_args__ = (Index("idx_language_family", "family"),)


class LSRMetadata(Base):
    """LSR metadata stored in PostgreSQL.

    The core LSR data and graph relationships are in Neo4j,
    but additional metadata and search indices are stored here.
    """

    __tablename__ = "lsr_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    neo4j_id = Column(String(100), unique=True, nullable=False, index=True)

    # Form data
    form_orthographic = Column(String(500), nullable=False, index=True)
    form_normalized = Column(String(500), index=True)
    form_phonetic = Column(String(500))

    # Language reference
    language_code = Column(String(10), ForeignKey("languages.code"), index=True)
    language = relationship("Language", back_populates="lsrs")

    # Temporal data
    date_start = Column(Integer)
    date_end = Column(Integer)
    date_confidence = Column(Float, default=1.0)
    period_label = Column(String(100))

    # Semantic data
    definition_primary = Column(Text)
    part_of_speech = Column(ARRAY(String))
    semantic_fields = Column(ARRAY(String))

    # Metadata
    source_databases = Column(ARRAY(String))
    confidence_overall = Column(Float, default=1.0)
    reconstruction_flag = Column(Boolean, default=False)
    human_validated = Column(Boolean, default=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    attestations = relationship("Attestation", back_populates="lsr", cascade="all, delete-orphan")
    ingestion_records = relationship("IngestionRecord", back_populates="lsr")

    __table_args__ = (
        Index("idx_lsr_form_language", "form_normalized", "language_code"),
        Index("idx_lsr_date_range", "date_start", "date_end"),
    )


class Attestation(Base):
    """Attestation records for LSRs."""

    __tablename__ = "attestations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    lsr_id = Column(UUID(as_uuid=True), ForeignKey("lsr_metadata.id"), nullable=False, index=True)

    # Source text
    text_excerpt = Column(Text)
    text_source = Column(String(500))
    text_date = Column(Integer)
    text_date_confidence = Column(Float, default=1.0)

    # Reference
    page_reference = Column(String(100))
    url = Column(String(1000))

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    lsr = relationship("LSRMetadata", back_populates="attestations")

    __table_args__ = (Index("idx_attestation_date", "text_date"),)


class IngestionRecord(Base):
    """Track data ingestion from external sources."""

    __tablename__ = "ingestion_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    lsr_id = Column(UUID(as_uuid=True), ForeignKey("lsr_metadata.id"), index=True)

    # Source information
    source_name = Column(String(100), nullable=False, index=True)
    source_id = Column(String(500))
    source_url = Column(String(1000))

    # Ingestion metadata
    ingested_at = Column(DateTime, default=datetime.utcnow)
    raw_data = Column(JSONB)
    processing_notes = Column(Text)

    # Status
    status = Column(String(50), default="processed")  # processed, failed, pending_review

    # Relationships
    lsr = relationship("LSRMetadata", back_populates="ingestion_records")

    __table_args__ = (
        UniqueConstraint("source_name", "source_id", name="uq_source_record"),
        Index("idx_ingestion_source", "source_name", "ingested_at"),
    )


class EntityResolutionLog(Base):
    """Log entity resolution decisions for auditing."""

    __tablename__ = "entity_resolution_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Resolution details
    source_entry_id = Column(String(500), nullable=False)
    action = Column(String(50), nullable=False)  # auto_merge, merge_with_flag, flag_for_review, create_new
    similarity_score = Column(Float)
    feature_scores = Column(JSONB)

    # Target (if merged)
    target_lsr_id = Column(UUID(as_uuid=True), ForeignKey("lsr_metadata.id"))
    created_lsr_id = Column(UUID(as_uuid=True), ForeignKey("lsr_metadata.id"))

    # Review status
    reviewed = Column(Boolean, default=False)
    reviewer_notes = Column(Text)
    reviewed_at = Column(DateTime)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("idx_resolution_action", "action", "created_at"),)


class AnalysisCache(Base):
    """Cache for expensive analysis results."""

    __tablename__ = "analysis_cache"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Cache key
    analysis_type = Column(String(100), nullable=False)  # text_dating, semantic_drift, etc.
    cache_key = Column(String(500), nullable=False, index=True)

    # Cached data
    result = Column(JSONB, nullable=False)
    parameters = Column(JSONB)

    # Validity
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, index=True)
    hit_count = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("analysis_type", "cache_key", name="uq_analysis_cache"),
        Index("idx_cache_type_key", "analysis_type", "cache_key"),
    )


class AuditLog(Base):
    """Audit log for data changes."""

    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    # Action details
    action = Column(String(50), nullable=False)  # create, update, delete, merge
    entity_type = Column(String(100), nullable=False)  # lsr, attestation, etc.
    entity_id = Column(UUID(as_uuid=True))

    # Change data
    old_values = Column(JSONB)
    new_values = Column(JSONB)

    # Context
    user_id = Column(String(100))
    request_id = Column(String(100))
    ip_address = Column(String(50))

    # Timestamp
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (Index("idx_audit_entity", "entity_type", "entity_id"),)
