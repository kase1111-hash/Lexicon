"""Initial database schema

Revision ID: 0001
Revises:
Create Date: 2026-01-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial database schema."""

    # Create languages table
    op.create_table(
        "languages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("family", sa.String(100), nullable=True),
        sa.Column("branch", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("status", sa.String(50), nullable=True, server_default="living"),
        sa.Column("speaker_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("idx_language_family", "languages", ["family"])
    op.create_index(op.f("ix_languages_code"), "languages", ["code"])

    # Create lsr_metadata table
    op.create_table(
        "lsr_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("neo4j_id", sa.String(100), nullable=False),
        sa.Column("form_orthographic", sa.String(500), nullable=False),
        sa.Column("form_normalized", sa.String(500), nullable=True),
        sa.Column("form_phonetic", sa.String(500), nullable=True),
        sa.Column("language_code", sa.String(10), nullable=True),
        sa.Column("date_start", sa.Integer(), nullable=True),
        sa.Column("date_end", sa.Integer(), nullable=True),
        sa.Column("date_confidence", sa.Float(), nullable=True, server_default="1.0"),
        sa.Column("period_label", sa.String(100), nullable=True),
        sa.Column("definition_primary", sa.Text(), nullable=True),
        sa.Column("part_of_speech", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("semantic_fields", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("source_databases", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("confidence_overall", sa.Float(), nullable=True, server_default="1.0"),
        sa.Column("reconstruction_flag", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("human_validated", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["language_code"], ["languages.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("neo4j_id"),
    )
    op.create_index("idx_lsr_date_range", "lsr_metadata", ["date_start", "date_end"])
    op.create_index("idx_lsr_form_language", "lsr_metadata", ["form_normalized", "language_code"])
    op.create_index(op.f("ix_lsr_metadata_form_orthographic"), "lsr_metadata", ["form_orthographic"])
    op.create_index(op.f("ix_lsr_metadata_form_normalized"), "lsr_metadata", ["form_normalized"])
    op.create_index(op.f("ix_lsr_metadata_language_code"), "lsr_metadata", ["language_code"])
    op.create_index(op.f("ix_lsr_metadata_neo4j_id"), "lsr_metadata", ["neo4j_id"])

    # Create attestations table
    op.create_table(
        "attestations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lsr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("text_excerpt", sa.Text(), nullable=True),
        sa.Column("text_source", sa.String(500), nullable=True),
        sa.Column("text_date", sa.Integer(), nullable=True),
        sa.Column("text_date_confidence", sa.Float(), nullable=True, server_default="1.0"),
        sa.Column("page_reference", sa.String(100), nullable=True),
        sa.Column("url", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["lsr_id"], ["lsr_metadata.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_attestation_date", "attestations", ["text_date"])
    op.create_index(op.f("ix_attestations_lsr_id"), "attestations", ["lsr_id"])

    # Create ingestion_records table
    op.create_table(
        "ingestion_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lsr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_name", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(500), nullable=True),
        sa.Column("source_url", sa.String(1000), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("processing_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=True, server_default="processed"),
        sa.ForeignKeyConstraint(["lsr_id"], ["lsr_metadata.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_name", "source_id", name="uq_source_record"),
    )
    op.create_index("idx_ingestion_source", "ingestion_records", ["source_name", "ingested_at"])
    op.create_index(op.f("ix_ingestion_records_lsr_id"), "ingestion_records", ["lsr_id"])
    op.create_index(op.f("ix_ingestion_records_source_name"), "ingestion_records", ["source_name"])

    # Create entity_resolution_logs table
    op.create_table(
        "entity_resolution_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_entry_id", sa.String(500), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("feature_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("target_lsr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_lsr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_lsr_id"], ["lsr_metadata.id"]),
        sa.ForeignKeyConstraint(["target_lsr_id"], ["lsr_metadata.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_resolution_action", "entity_resolution_logs", ["action", "created_at"])

    # Create analysis_cache table
    op.create_table(
        "analysis_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_type", sa.String(100), nullable=False),
        sa.Column("cache_key", sa.String(500), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("hit_count", sa.Integer(), nullable=True, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("analysis_type", "cache_key", name="uq_analysis_cache"),
    )
    op.create_index("idx_cache_type_key", "analysis_cache", ["analysis_type", "cache_key"])
    op.create_index(op.f("ix_analysis_cache_cache_key"), "analysis_cache", ["cache_key"])
    op.create_index(op.f("ix_analysis_cache_expires_at"), "analysis_cache", ["expires_at"])

    # Create audit_logs table
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("old_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("request_id", sa.String(100), nullable=True),
        sa.Column("ip_address", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_table("audit_logs")
    op.drop_table("analysis_cache")
    op.drop_table("entity_resolution_logs")
    op.drop_table("ingestion_records")
    op.drop_table("attestations")
    op.drop_table("lsr_metadata")
    op.drop_table("languages")
