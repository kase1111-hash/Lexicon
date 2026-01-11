"""Test package pipeline validation.

This test module validates that the entire package pipeline works correctly,
including imports, model creation, utilities, and build system integration.
"""

import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest


class TestPackageImports:
    """Test that all package modules can be imported correctly."""

    def test_main_package_import(self):
        """Test that the main package can be imported."""
        import src

        assert hasattr(src, "__version__")
        assert src.__version__ == "0.1.0"

    def test_config_imports(self):
        """Test configuration module imports."""
        from src import Settings, get_settings, is_debug, is_production

        assert Settings is not None
        assert callable(get_settings)
        assert callable(is_debug)
        assert callable(is_production)

    def test_exceptions_imports(self):
        """Test exception module imports."""
        from src import (
            AnalysisError,
            ConfigurationError,
            DatabaseError,
            LexiconError,
            NotFoundError,
            PipelineError,
            ValidationError,
        )

        # Verify exception hierarchy
        assert issubclass(NotFoundError, LexiconError)
        assert issubclass(ValidationError, LexiconError)
        assert issubclass(DatabaseError, LexiconError)
        assert issubclass(PipelineError, LexiconError)
        assert issubclass(AnalysisError, LexiconError)
        assert issubclass(ConfigurationError, LexiconError)

    def test_models_imports(self):
        """Test models module imports."""
        from src.models import LSR, Attestation, Edge, Language, RelationshipType

        assert LSR is not None
        assert Attestation is not None
        assert Language is not None
        assert Edge is not None
        assert RelationshipType is not None

    def test_utils_imports(self):
        """Test utilities module imports."""
        from src.utils.phonetics import PhoneticUtils
        from src.utils.validation import sanitize_string, is_valid_iso639_3

        assert PhoneticUtils is not None
        assert callable(sanitize_string)
        assert callable(is_valid_iso639_3)

    def test_pipelines_imports(self):
        """Test pipeline module imports."""
        from src.pipelines import Validator
        from src.pipelines.base import BasePipeline

        assert BasePipeline is not None
        assert Validator is not None


class TestModelFunctionality:
    """Test core model functionality."""

    def test_lsr_creation_and_normalization(self):
        """Test LSR creation with automatic normalization."""
        from src.models.lsr import LSR

        lsr = LSR(
            form_orthographic="Château",
            language_code="fra",
            definition_primary="castle",
        )

        assert lsr.form_orthographic == "Château"
        assert lsr.language_code == "fra"
        assert isinstance(lsr.id, UUID)
        # Auto-normalization should strip diacritics
        assert lsr.form_normalized == "chateau"

    def test_lsr_date_validation(self):
        """Test LSR date range validation."""
        from src.models.lsr import LSR

        # Valid date range
        lsr = LSR(date_start=1000, date_end=2000)
        assert lsr.date_start == 1000
        assert lsr.date_end == 2000

        # Invalid date range should raise
        with pytest.raises(ValueError, match="date_end must be >= date_start"):
            LSR(date_start=2000, date_end=1000)

    def test_lsr_attestation_management(self):
        """Test adding attestations to LSR."""
        from src.models.lsr import Attestation, LSR

        lsr = LSR(form_orthographic="water")
        att = Attestation(
            text_excerpt="The water flows",
            text_source="Test Corpus",
            text_date=1450,
        )

        lsr.add_attestation(att)

        assert len(lsr.attestations) == 1
        assert lsr.attestations[0].lsr_id == lsr.id
        assert lsr.date_start == 1450

    def test_lsr_confidence_calculation(self):
        """Test confidence score calculation."""
        from src.models.lsr import Attestation, LSR

        lsr = LSR(form_orthographic="test")

        # Add attestations to boost confidence
        for i in range(5):
            lsr.add_attestation(
                Attestation(text_excerpt=f"Example {i}", text_source="Corpus", text_date=1500 + i)
            )

        lsr.update_confidence()
        # Confidence should be higher with more attestations
        assert lsr.confidence_overall > 0.5

    def test_language_model(self):
        """Test Language model creation."""
        from src.models.language import Language

        lang = Language(
            iso_code="lat",
            name="Latin",
            family="Indo-European",
            is_living=False,
        )

        assert lang.iso_code == "lat"
        assert lang.is_living is False

    def test_edge_model(self):
        """Test Edge model for relationships."""
        from uuid import uuid4

        from src.models.relationships import Edge, RelationshipType

        source_id = uuid4()
        target_id = uuid4()
        edge = Edge(
            source_id=source_id,
            target_id=target_id,
            relationship_type=RelationshipType.COGNATE_OF,
            confidence=0.85,
        )

        assert edge.relationship_type == RelationshipType.COGNATE_OF
        assert edge.confidence == 0.85
        assert edge.source_id == source_id
        assert edge.target_id == target_id


class TestUtilities:
    """Test utility functions."""

    def test_phonetic_utils_strip_diacritics(self):
        """Test diacritic stripping."""
        from src.utils.phonetics import PhoneticUtils

        assert PhoneticUtils.strip_diacritics("café") == "cafe"
        assert PhoneticUtils.strip_diacritics("naïve") == "naive"
        assert PhoneticUtils.strip_diacritics("Zürich") == "Zurich"
        assert PhoneticUtils.strip_diacritics("résumé") == "resume"

    def test_phonetic_utils_normalize_ipa(self):
        """Test IPA normalization."""
        from src.utils.phonetics import PhoneticUtils

        # Should return NFC normalized string
        result = PhoneticUtils.normalize_ipa("wɔːtər")
        assert isinstance(result, str)

    def test_phonetic_utils_levenshtein(self):
        """Test Levenshtein distance calculation."""
        from src.utils.phonetics import PhoneticUtils

        assert PhoneticUtils.levenshtein_distance("kitten", "sitting") == 3
        assert PhoneticUtils.levenshtein_distance("hello", "hello") == 0
        assert PhoneticUtils.levenshtein_distance("", "abc") == 3
        assert PhoneticUtils.levenshtein_distance("abc", "") == 3


class TestPipelineIntegration:
    """Test pipeline integration."""

    def test_validator_initialization(self):
        """Test validator can be initialized."""
        from src.pipelines import Validator

        validator = Validator()
        assert validator is not None
        assert hasattr(validator, "run_all")

    def test_validator_with_valid_lsr(self):
        """Test validator processes valid LSR."""
        from src.models.lsr import LSR
        from src.pipelines import Validator

        validator = Validator()
        lsr = LSR(
            form_orthographic="test",
            language_code="eng",
            definition_primary="a test word",
        )

        # Convert LSR to dict for validation
        lsr_dict = {"id": lsr.id, **lsr.model_dump()}
        result = validator.run_all(lsr_dict)
        assert result is not None
        assert result.lsr_id == lsr.id


class TestGraphSerialization:
    """Test data serialization for storage."""

    def test_lsr_to_graph_node(self):
        """Test LSR conversion to graph node format."""
        from src.models.lsr import LSR

        lsr = LSR(
            form_orthographic="house",
            language_code="eng",
            definition_primary="a building for dwelling",
            date_start=1200,
            date_end=2024,
        )

        node = lsr.to_graph_node()

        assert node["form"] == "house"
        assert node["language_code"] == "eng"
        assert node["definition"] == "a building for dwelling"
        assert node["date_start"] == 1200
        assert node["date_end"] == 2024
        assert "id" in node

    def test_lsr_to_search_document(self):
        """Test LSR conversion to search document format."""
        from src.models.lsr import LSR

        lsr = LSR(
            form_orthographic="Wasser",
            form_phonetic="ˈvasɐ",
            language_code="deu",
            language_name="German",
            language_family="Indo-European",
            definition_primary="water",
            part_of_speech=["noun"],
        )

        doc = lsr.to_search_document()

        assert doc["form_orthographic"] == "Wasser"
        assert doc["form_phonetic"] == "ˈvasɐ"
        assert doc["language_code"] == "deu"
        assert doc["language_name"] == "German"
        assert doc["language_family"] == "Indo-European"
        assert "noun" in doc["part_of_speech"]


class TestLSRMerging:
    """Test LSR merging functionality for entity resolution."""

    def test_merge_attestations(self):
        """Test merging attestations from two LSRs."""
        from src.models.lsr import Attestation, LSR

        lsr1 = LSR(form_orthographic="word")
        lsr1.add_attestation(
            Attestation(text_excerpt="First source", text_source="A", text_date=1400)
        )

        lsr2 = LSR(form_orthographic="word")
        lsr2.add_attestation(
            Attestation(text_excerpt="Second source", text_source="B", text_date=1500)
        )

        lsr1.merge_with(lsr2)

        assert len(lsr1.attestations) == 2
        assert lsr1.date_start == 1400
        assert lsr1.date_end == 1500
        assert lsr1.version == 2

    def test_merge_source_databases(self):
        """Test merging source database lists."""
        from src.models.lsr import LSR

        lsr1 = LSR(form_orthographic="word", source_databases=["wiktionary"])
        lsr2 = LSR(form_orthographic="word", source_databases=["clld", "wiktionary"])

        lsr1.merge_with(lsr2)

        assert "wiktionary" in lsr1.source_databases
        assert "clld" in lsr1.source_databases
        assert len(lsr1.source_databases) == 2


class TestBuildSystem:
    """Test that the build system works correctly."""

    def test_pyproject_toml_exists(self):
        """Test that pyproject.toml exists and is valid."""
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        assert pyproject.exists(), "pyproject.toml not found"

        content = pyproject.read_text()
        assert "[project]" in content
        assert 'name = "linguistic-stratigraphy"' in content
        assert "[build-system]" in content

    def test_version_file_exists(self):
        """Test that VERSION file exists and matches package version."""
        version_file = Path(__file__).parent.parent.parent / "VERSION"
        assert version_file.exists(), "VERSION file not found"

        from src import __version__

        file_version = version_file.read_text().strip()
        assert file_version == __version__, f"VERSION file ({file_version}) doesn't match package ({__version__})"

    def test_package_structure(self):
        """Test that package structure is correct."""
        src_dir = Path(__file__).parent.parent.parent / "src"
        assert src_dir.exists()
        assert (src_dir / "__init__.py").exists()
        assert (src_dir / "models").is_dir()
        assert (src_dir / "pipelines").is_dir()
        assert (src_dir / "utils").is_dir()
        assert (src_dir / "api").is_dir()

    def test_build_command_available(self):
        """Test that the build module is available."""
        result = subprocess.run(
            [sys.executable, "-m", "build", "--help"],
            capture_output=True,
            text=True,
        )
        # Build module should either work or give import error
        # (it's optional for tests, required for actual builds)
        assert result.returncode == 0 or "No module named build" in result.stderr


class TestEnumValues:
    """Test enum values are correct."""

    def test_date_source_enum(self):
        """Test DateSource enum values."""
        from src.models.lsr import DateSource

        assert DateSource.ATTESTED.value == "ATTESTED"
        assert DateSource.INTERPOLATED.value == "INTERPOLATED"
        assert DateSource.RECONSTRUCTED.value == "RECONSTRUCTED"

    def test_register_enum(self):
        """Test Register enum values."""
        from src.models.lsr import Register

        assert Register.FORMAL.value == "FORMAL"
        assert Register.COLLOQUIAL.value == "COLLOQUIAL"
        assert Register.TECHNICAL.value == "TECHNICAL"
        assert Register.SACRED.value == "SACRED"
        assert Register.LITERARY.value == "LITERARY"
        assert Register.SLANG.value == "SLANG"

    def test_relationship_type_enum(self):
        """Test RelationshipType enum values."""
        from src.models.relationships import RelationshipType

        assert RelationshipType.DESCENDS_FROM is not None
        assert RelationshipType.COGNATE_OF is not None
        assert RelationshipType.BORROWED_FROM is not None
        assert RelationshipType.SHIFTED_TO is not None
        assert RelationshipType.MERGED_WITH is not None
