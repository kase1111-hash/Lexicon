"""Phase 2 integration tests: relationship extraction, validation, adapter improvements.

Tests cover:
1. Etymology text parsing -> RawRelationship extraction
2. Template parsing -> RawRelationship extraction
3. Cognate detection via edit distance
4. Validation pipeline (schema, consistency, anomalies)
5. Wiktionary adapter improvements (multi-etymology, template extraction)
6. CLI extract-relationships command logic
"""

import pytest
from uuid import uuid4

from src.models.lsr import LSR
from src.pipelines.relationship_extraction import (
    RelationshipExtractor,
    RelationshipType,
    RawRelationship,
    ExtractedRelationship,
)
from src.pipelines.validation import (
    ValidationResult,
    Validator,
)


class TestEtymologyTextParsing:
    """Test extraction of relationships from plain etymology text."""

    @pytest.fixture
    def extractor(self):
        return RelationshipExtractor()

    def test_from_old_english(self, extractor):
        """'From Old English X' -> DESCENDS_FROM."""
        rels = extractor.extract_from_etymology_text(
            "From Old English wæter, from Proto-Germanic *watōr"
        )
        assert len(rels) >= 1
        ang_rels = [r for r in rels if r.target_language_code == "ang"]
        assert len(ang_rels) == 1
        assert ang_rels[0].relationship_type == RelationshipType.DESCENDS_FROM
        assert ang_rels[0].target_form == "wæter"

    def test_from_proto_germanic(self, extractor):
        """'From Proto-Germanic *X' -> DESCENDS_FROM with reconstructed flag."""
        rels = extractor.extract_from_etymology_text(
            "From Proto-Germanic *watōr"
        )
        gem_rels = [r for r in rels if r.target_language_code == "gem-pro"]
        assert len(gem_rels) >= 1
        assert gem_rels[0].is_reconstructed is True
        assert gem_rels[0].target_form == "watōr"

    def test_borrowed_from_french(self, extractor):
        """'Borrowed from French X' -> BORROWED_FROM."""
        rels = extractor.extract_from_etymology_text(
            "Borrowed from French justice, from Latin iustitia"
        )
        fra_rels = [r for r in rels if r.target_language_code == "fra"]
        assert len(fra_rels) == 1
        assert fra_rels[0].relationship_type == RelationshipType.BORROWED_FROM
        assert fra_rels[0].target_form == "justice"

    def test_cognate_with_german(self, extractor):
        """'Cognate with German X' -> COGNATE_OF."""
        rels = extractor.extract_from_etymology_text(
            "From Old English wæter. Cognate with German Wasser"
        )
        deu_rels = [r for r in rels if r.target_language_code == "deu"]
        assert len(deu_rels) == 1
        assert deu_rels[0].relationship_type == RelationshipType.COGNATE_OF
        assert deu_rels[0].target_form == "Wasser"

    def test_multiple_relationships(self, extractor):
        """Complex etymology with multiple relationships."""
        text = (
            "From Middle English water, from Old English wæter, "
            "from Proto-Germanic *watōr. Cognate with German Wasser, "
            "Dutch water."
        )
        rels = extractor.extract_from_etymology_text(text)
        assert len(rels) >= 3  # At least ME, OE, and German cognate

        types = {r.relationship_type for r in rels}
        assert RelationshipType.DESCENDS_FROM in types

    def test_empty_text(self, extractor):
        """Empty or None text returns no relationships."""
        assert extractor.extract_from_etymology_text("") == []
        assert extractor.extract_from_etymology_text(None) == []

    def test_unrecognized_language(self, extractor):
        """Unrecognized language names are skipped."""
        rels = extractor.extract_from_etymology_text(
            "From Klingon tlhIngan"
        )
        assert len(rels) == 0

    def test_borrowed_context_detection(self, extractor):
        """Context before 'from' can change type to BORROWED_FROM."""
        rels = extractor.extract_from_etymology_text(
            "Loan from Latin computare"
        )
        # "Loan ... from Latin" context should produce BORROWED_FROM
        lat_rels = [r for r in rels if r.target_language_code == "lat"]
        if lat_rels:
            assert lat_rels[0].relationship_type == RelationshipType.BORROWED_FROM


class TestTemplateParsing:
    """Test extraction of relationships from Wiktionary templates."""

    @pytest.fixture
    def extractor(self):
        return RelationshipExtractor()

    def test_inh_template(self, extractor):
        """{{inh}} template -> DESCENDS_FROM."""
        templates = [{"name": "inh", "lang": "ang", "term": "wæter"}]
        rels = extractor.extract_from_templates(templates)
        assert len(rels) == 1
        assert rels[0].relationship_type == RelationshipType.DESCENDS_FROM
        assert rels[0].target_form == "wæter"
        assert rels[0].target_language_code == "ang"
        assert rels[0].confidence == 0.9

    def test_bor_template(self, extractor):
        """{{bor}} template -> BORROWED_FROM."""
        templates = [{"name": "bor", "lang": "fra", "term": "justice"}]
        rels = extractor.extract_from_templates(templates)
        assert len(rels) == 1
        assert rels[0].relationship_type == RelationshipType.BORROWED_FROM

    def test_cog_template(self, extractor):
        """{{cog}} template -> COGNATE_OF."""
        templates = [{"name": "cog", "lang": "deu", "term": "Wasser"}]
        rels = extractor.extract_from_templates(templates)
        assert len(rels) == 1
        assert rels[0].relationship_type == RelationshipType.COGNATE_OF

    def test_der_template(self, extractor):
        """{{der}} template -> DESCENDS_FROM (default)."""
        templates = [{"name": "der", "lang": "lat", "term": "aqua"}]
        rels = extractor.extract_from_templates(templates)
        assert len(rels) == 1
        assert rels[0].relationship_type == RelationshipType.DESCENDS_FROM
        assert rels[0].confidence == 0.7  # Lower than inh/bor

    def test_reconstructed_form(self, extractor):
        """Reconstructed form (starting with *) sets flag."""
        templates = [{"name": "inh", "lang": "gem-pro", "term": "*watōr"}]
        rels = extractor.extract_from_templates(templates)
        assert len(rels) == 1
        assert rels[0].is_reconstructed is True
        assert rels[0].target_form == "watōr"  # * stripped

    def test_empty_templates(self, extractor):
        """Empty template list returns no relationships."""
        assert extractor.extract_from_templates([]) == []

    def test_template_missing_fields(self, extractor):
        """Templates without required fields are skipped."""
        templates = [
            {"name": "inh", "lang": "eng"},  # No term
            {"name": "bor", "term": "word"},  # No lang
        ]
        rels = extractor.extract_from_templates(templates)
        assert len(rels) == 0


class TestCognateDetection:
    """Test cognate detection via form similarity."""

    @pytest.fixture
    def store_and_extractor(self):
        water_eng = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            language_name="English",
            definition_primary="a clear liquid",
        )
        wasser_deu = LSR(
            form_orthographic="Wasser",
            form_normalized="wasser",
            language_code="deu",
            language_name="German",
            definition_primary="water",
        )
        eau_fra = LSR(
            form_orthographic="eau",
            form_normalized="eau",
            language_code="fra",
            language_name="French",
            definition_primary="water",
        )
        store = {
            water_eng.id: water_eng,
            wasser_deu.id: wasser_deu,
            eau_fra.id: eau_fra,
        }
        extractor = RelationshipExtractor(lsr_store=store)
        return store, extractor, water_eng, wasser_deu, eau_fra

    def test_similar_forms_detected(self, store_and_extractor):
        """water ~ wasser should be detected as cognate."""
        store, extractor, water_eng, wasser_deu, eau_fra = store_and_extractor
        cognates = extractor.detect_cognates(
            water_eng.id, [wasser_deu.id, eau_fra.id], min_similarity=0.5
        )
        # water vs wasser should have high similarity
        deu_cognates = [c for c in cognates if c.target_id == wasser_deu.id]
        assert len(deu_cognates) == 1
        assert deu_cognates[0].relationship_type == RelationshipType.COGNATE_OF

    def test_dissimilar_forms_excluded(self, store_and_extractor):
        """water ~ eau should not be detected with high threshold."""
        store, extractor, water_eng, wasser_deu, eau_fra = store_and_extractor
        cognates = extractor.detect_cognates(
            water_eng.id, [eau_fra.id], min_similarity=0.8
        )
        assert len(cognates) == 0

    def test_same_language_excluded(self, store_and_extractor):
        """Same-language forms should not be detected as cognates."""
        store, extractor, water_eng, _, _ = store_and_extractor
        # Add another English word
        fire_eng = LSR(
            form_orthographic="water",
            form_normalized="water",
            language_code="eng",
            definition_primary="same word",
        )
        store[fire_eng.id] = fire_eng
        extractor.set_lsr_store(store)

        cognates = extractor.detect_cognates(water_eng.id, [fire_eng.id])
        assert len(cognates) == 0

    def test_empty_candidates(self, store_and_extractor):
        store, extractor, water_eng, _, _ = store_and_extractor
        assert extractor.detect_cognates(water_eng.id, []) == []


class TestValidationSchema:
    """Test schema validation."""

    @pytest.fixture
    def validator(self):
        return Validator()

    def test_valid_lsr(self, validator):
        """Valid LSR passes schema validation."""
        lsr = {
            "form_orthographic": "water",
            "language_code": "eng",
            "date_start": 1200,
            "date_end": 2024,
            "definition_primary": "a clear liquid",
            "source_databases": ["wiktionary"],
        }
        passed, issues = validator.validate_schema(lsr)
        assert passed is True
        critical = [i for i in issues if i["severity"] == "critical"]
        assert len(critical) == 0

    def test_missing_form(self, validator):
        """Missing form_orthographic -> critical."""
        lsr = {"language_code": "eng"}
        passed, issues = validator.validate_schema(lsr)
        assert passed is False
        assert any(i["field"] == "form_orthographic" for i in issues)

    def test_missing_language_code(self, validator):
        """Missing language_code -> critical."""
        lsr = {"form_orthographic": "water"}
        passed, issues = validator.validate_schema(lsr)
        assert passed is False
        assert any(i["field"] == "language_code" for i in issues)

    def test_inverted_date_range(self, validator):
        """date_end < date_start -> critical."""
        lsr = {
            "form_orthographic": "water",
            "language_code": "eng",
            "date_start": 2024,
            "date_end": 1200,
        }
        passed, issues = validator.validate_schema(lsr)
        assert passed is False
        assert any(i["field"] == "date_range" for i in issues)

    def test_unknown_language_code(self, validator):
        """Unknown language code -> warning (not critical)."""
        lsr = {
            "form_orthographic": "xyzzy",
            "language_code": "xxx",
        }
        passed, issues = validator.validate_schema(lsr)
        assert passed is True  # Warnings don't fail
        assert any(i["field"] == "language_code" and i["severity"] == "warning" for i in issues)

    def test_confidence_out_of_range(self, validator):
        """Confidence > 1.0 -> warning."""
        lsr = {
            "form_orthographic": "water",
            "language_code": "eng",
            "confidence_overall": 1.5,
        }
        passed, issues = validator.validate_schema(lsr)
        assert any(i["field"] == "confidence_overall" for i in issues)


class TestValidationConsistency:
    """Test consistency validation."""

    @pytest.fixture
    def validator(self):
        return Validator()

    def test_no_ancestors_passes(self, validator):
        """LSR without ancestors passes consistency."""
        lsr = {"id": str(uuid4()), "form_orthographic": "water"}
        passed, issues = validator.validate_consistency(lsr)
        assert passed is True

    def test_circular_ancestry(self, validator):
        """Circular ancestry is detected."""
        id1 = str(uuid4())
        lsr = {"id": id1, "form_orthographic": "water"}
        ancestors = [
            {"id": "a1", "form_orthographic": "wæter"},
            {"id": "a2", "form_orthographic": "watōr"},
            {"id": "a1", "form_orthographic": "wæter"},  # Circular!
        ]
        passed, issues = validator.validate_consistency(lsr, ancestor_chain=ancestors)
        assert passed is False
        assert any("Circular" in i["message"] for i in issues)

    def test_self_reference(self, validator):
        """Self-reference as ancestor is detected."""
        lsr_id = str(uuid4())
        lsr = {"id": lsr_id, "form_orthographic": "water"}
        ancestors = [{"id": lsr_id, "form_orthographic": "water"}]
        passed, issues = validator.validate_consistency(lsr, ancestor_chain=ancestors)
        assert passed is False
        assert any("itself" in i["message"] for i in issues)

    def test_temporal_inconsistency(self, validator):
        """Ancestor that postdates descendant produces warning."""
        lsr = {"id": str(uuid4()), "form_orthographic": "water", "date_start": 1200}
        ancestors = [
            {"id": "anc1", "form_orthographic": "modern", "date_start": 1900},
        ]
        passed, issues = validator.validate_consistency(lsr, ancestor_chain=ancestors)
        # Temporal issues are warnings, not critical
        assert any("postdates" in i["message"] for i in issues)


class TestValidationAnomalies:
    """Test anomaly detection."""

    @pytest.fixture
    def validator(self):
        return Validator()

    def test_low_confidence_flagged(self, validator):
        """Low confidence score produces warning."""
        lsr = {
            "form_orthographic": "test",
            "language_code": "eng",
            "confidence_overall": 0.1,
            "definition_primary": "test",
            "source_databases": ["test"],
        }
        passed, issues = validator.detect_anomalies(lsr)
        assert any(i["field"] == "confidence_overall" for i in issues)

    def test_missing_definition_flagged(self, validator):
        """Missing definition produces warning."""
        lsr = {
            "form_orthographic": "test",
            "language_code": "eng",
            "source_databases": ["test"],
        }
        passed, issues = validator.detect_anomalies(lsr)
        assert any(i["field"] == "definition_primary" for i in issues)

    def test_wide_date_range_flagged(self, validator):
        """Very wide date range produces info."""
        lsr = {
            "form_orthographic": "test",
            "language_code": "eng",
            "date_start": 500,
            "date_end": 2024,
            "definition_primary": "test",
            "source_databases": ["test"],
        }
        passed, issues = validator.detect_anomalies(lsr)
        assert any(i["field"] == "date_range" for i in issues)

    def test_clean_lsr_no_anomalies(self, validator):
        """Well-formed LSR has no critical anomalies."""
        lsr = {
            "form_orthographic": "water",
            "language_code": "eng",
            "date_start": 1200,
            "date_end": 1400,
            "definition_primary": "a clear liquid",
            "source_databases": ["wiktionary"],
            "confidence_overall": 0.9,
        }
        passed, issues = validator.detect_anomalies(lsr)
        assert passed is True
        # May have info-level issues for date range, that's fine
        critical = [i for i in issues if i["severity"] == "critical"]
        assert len(critical) == 0


class TestValidationRunAll:
    """Test the run_all orchestrator."""

    def test_valid_lsr_passes(self):
        validator = Validator()
        lsr = {
            "id": str(uuid4()),
            "form_orthographic": "water",
            "language_code": "eng",
            "language_name": "English",
            "date_start": 1200,
            "date_end": 2024,
            "definition_primary": "a clear liquid",
            "source_databases": ["wiktionary"],
        }
        report = validator.run_all(lsr)
        assert report.result in (ValidationResult.PASS, ValidationResult.WARN)
        assert len(report.validators_run) == 4

    def test_invalid_lsr_fails(self):
        validator = Validator()
        lsr = {}  # Empty dict, missing everything
        report = validator.run_all(lsr)
        assert report.result == ValidationResult.FAIL
        assert len(report.issues) > 0

    def test_strict_mode(self):
        """Strict mode treats warnings as failures."""
        validator = Validator(strict=True)
        lsr = {
            "form_orthographic": "test",
            "language_code": "xxx",  # Unknown code = warning
        }
        report = validator.run_all(lsr)
        assert report.result == ValidationResult.FAIL

    def test_batch_validation(self):
        validator = Validator()
        lsrs = [
            {"form_orthographic": "water", "language_code": "eng",
             "definition_primary": "liquid", "source_databases": ["wikt"]},
            {"form_orthographic": "", "language_code": ""},
        ]
        reports = validator.validate_batch(lsrs)
        assert len(reports) == 2
        assert reports[0].result != ValidationResult.FAIL  # Valid
        assert reports[1].result == ValidationResult.FAIL  # Invalid


class TestWiktionaryEtymologyParsing:
    """Test improved Wiktionary adapter etymology extraction."""

    def test_extract_etymology_raw_single(self):
        """Single etymology section extracted correctly."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        content = """===Etymology===
From {{inh|en|enm|water}}, from {{inh|en|ang|wæter}}.

===Noun===
# A clear liquid.
"""
        raw = adapter._extract_etymology_raw(content)
        assert raw is not None
        assert "{{inh|en|enm|water}}" in raw
        assert "{{inh|en|ang|wæter}}" in raw

    def test_extract_etymology_raw_numbered(self):
        """Numbered etymology sections: Etymology 1 is extracted."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        content = """===Etymology 1===
From {{inh|en|ang|bæc||back, rear}}.

===Noun===
# The rear side.

===Etymology 2===
From {{bor|en|nl|bak}}.

===Noun===
# A container.
"""
        raw = adapter._extract_etymology_raw(content)
        assert raw is not None
        assert "{{inh|en|ang|bæc" in raw
        assert "{{bor|en|nl|bak}}" not in raw  # Etymology 2 not included

    def test_extract_etymology_templates(self):
        """Templates are parsed from raw etymology text."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        raw = "From {{inh|en|enm|water}}, from {{inh|en|ang|wæter}}, from {{inh|en|gem-pro|*watōr}}."
        templates = adapter._extract_etymology_templates(raw)

        assert len(templates) == 3
        assert templates[0]["name"] == "inh"
        assert templates[0]["term"] == "water"
        assert templates[1]["term"] == "wæter"
        assert templates[2]["term"] == "*watōr"

    def test_extract_borrowing_template(self):
        """Borrowing templates are extracted."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        raw = "{{bor|en|fr|justice}}, from {{der|en|la|iustitia}}."
        templates = adapter._extract_etymology_templates(raw)

        assert len(templates) == 2
        assert templates[0]["name"] == "bor"
        assert templates[0]["lang"] == "fr"
        assert templates[0]["term"] == "justice"
        assert templates[1]["name"] == "der"

    def test_extract_cognate_template(self):
        """Cognate templates are extracted."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        raw = "Cognate with {{cog|de|Wasser}}, {{cog|nl|water}}."
        templates = adapter._extract_etymology_templates(raw)

        assert len(templates) == 2
        assert templates[0]["name"] == "cog"
        assert templates[0]["lang"] == "de"
        assert templates[0]["term"] == "Wasser"

    def test_clean_wikitext(self):
        """Wikitext cleaning removes markup."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        text = "From {{inh|en|ang|wæter}}, ''meaning'' [[water]]."
        cleaned = adapter._clean_wikitext(text)
        assert "{{" not in cleaned
        assert "''" not in cleaned
        assert "[[" not in cleaned
        assert "water" in cleaned

    def test_parse_language_section_with_templates(self):
        """Full language section parsing preserves template data."""
        from src.adapters.wiktionary import WiktionaryAdapter
        adapter = WiktionaryAdapter()

        content = """===Etymology===
From {{inh|en|enm|water}}, from {{inh|en|ang|wæter}}.

===Noun===
# A clear, colorless liquid.
# A body of water.
"""
        entry = adapter._parse_language_section("water", "English", "eng", content)
        assert entry is not None
        assert entry.form == "water"
        assert entry.language_code == "eng"
        assert len(entry.related_forms) == 2
        assert entry.related_forms[0]["type"] == "inh"
        assert entry.related_forms[0]["form"] == "water"
        assert "etymology_templates" in entry.raw_data
        assert len(entry.raw_data["etymology_templates"]) == 2


class TestCLILogic:
    """Test CLI helper functions (not the full argparse interface)."""

    def test_extract_relationships_text(self):
        """RelationshipExtractor works from CLI context."""
        extractor = RelationshipExtractor()
        rels = extractor.extract_from_etymology_text(
            "From Old English wæter, from Proto-Germanic *watōr"
        )
        assert len(rels) >= 1

    def test_validator_from_cli_context(self):
        """Validator works with CLI-style dict input."""
        validator = Validator()
        report = validator.run_all({
            "form_orthographic": "water",
            "language_code": "eng",
            "definition_primary": "a clear liquid",
            "source_databases": ["cli-test"],
        })
        assert report.result in (ValidationResult.PASS, ValidationResult.WARN)
