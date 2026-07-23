"""Unit tests for utility modules."""

from src.utils.embeddings import EmbeddingUtils
from src.utils.phonetics import PhoneticUtils


class TestPhoneticUtils:
    """Tests for PhoneticUtils."""

    def test_strip_diacritics(self):
        """Test diacritic stripping."""
        result = PhoneticUtils.strip_diacritics("café")
        assert result == "cafe"

    def test_strip_diacritics_complex(self):
        """Test diacritic stripping with complex characters."""
        result = PhoneticUtils.strip_diacritics("naïve résumé")
        assert result == "naive resume"

    def test_levenshtein_distance_equal(self):
        """Test Levenshtein distance for equal strings."""
        result = PhoneticUtils.levenshtein_distance("test", "test")
        assert result == 0

    def test_levenshtein_distance_different(self):
        """Test Levenshtein distance for different strings."""
        result = PhoneticUtils.levenshtein_distance("kitten", "sitting")
        assert result == 3

    def test_levenshtein_distance_empty(self):
        """Test Levenshtein distance with empty string."""
        result = PhoneticUtils.levenshtein_distance("test", "")
        assert result == 4

    def test_soundex_standard_examples(self):
        """Test Soundex against the canonical reference codes."""
        assert PhoneticUtils.soundex("Robert") == "R163"
        assert PhoneticUtils.soundex("Rupert") == "R163"
        assert PhoneticUtils.soundex("Ashcraft") == "A261"
        assert PhoneticUtils.soundex("Tymczak") == "T522"
        assert PhoneticUtils.soundex("Pfister") == "P236"
        assert PhoneticUtils.soundex("Honeyman") == "H555"

    def test_soundex_similar_words_match(self):
        """Similar-sounding words should share a Soundex code."""
        assert PhoneticUtils.soundex("Smith") == PhoneticUtils.soundex("Smyth")

    def test_soundex_empty_and_nonalpha(self):
        """Soundex of empty or non-alphabetic input is empty."""
        assert PhoneticUtils.soundex("") == ""
        assert PhoneticUtils.soundex("123") == ""

    def test_soundex_diacritics(self):
        """Diacritics are stripped before encoding."""
        assert PhoneticUtils.soundex("café") == PhoneticUtils.soundex("cafe")

    def test_metaphone_basics(self):
        """Test Metaphone codes for common transformations."""
        assert PhoneticUtils.metaphone("city") == "ST"
        assert PhoneticUtils.metaphone("knight") == "NT"
        assert PhoneticUtils.metaphone("phone") == "FN"
        assert PhoneticUtils.metaphone("shell") == "XL"
        assert PhoneticUtils.metaphone("thing") == "0NK"

    def test_metaphone_similar_words_match(self):
        """Homophones should share a Metaphone code."""
        assert PhoneticUtils.metaphone("write") == PhoneticUtils.metaphone("rite")

    def test_metaphone_empty(self):
        """Metaphone of empty input is empty."""
        assert PhoneticUtils.metaphone("") == ""

    def test_phonetic_distance_identical(self):
        """Identical IPA strings have zero distance."""
        assert PhoneticUtils.phonetic_distance("wɔtər", "wɔtər") == 0.0

    def test_phonetic_distance_close_sounds(self):
        """Voicing-only differences are closer than unrelated sounds."""
        voicing_diff = PhoneticUtils.phonetic_distance("pat", "bat")
        unrelated = PhoneticUtils.phonetic_distance("pat", "mig")
        assert 0.0 < voicing_diff < unrelated <= 1.0

    def test_phonetic_distance_empty(self):
        """Empty vs non-empty is maximal; empty vs empty is zero."""
        assert PhoneticUtils.phonetic_distance("", "") == 0.0
        assert PhoneticUtils.phonetic_distance("a", "") == 1.0

    def test_phonetic_distance_symmetric(self):
        """Distance is symmetric."""
        d1 = PhoneticUtils.phonetic_distance("kat", "gat")
        d2 = PhoneticUtils.phonetic_distance("gat", "kat")
        assert d1 == d2

    def test_normalize_ipa_strips_suprasegmentals(self):
        """Stress marks and syllable breaks are removed."""
        assert PhoneticUtils.normalize_ipa("ˈwɔː.tər") == "wɔːtər"

    def test_normalize_ipa_ascii_substitutes(self):
        """ASCII colon becomes length mark; g becomes IPA script g."""
        assert PhoneticUtils.normalize_ipa("wa:g") == "waːɡ"

    def test_apply_sound_law_grimm(self):
        """Grimm's law shifts each stop series exactly once."""
        # Voiceless stop fricativizes: *pater-like p > ɸ
        assert PhoneticUtils.apply_sound_law("pater", "grimm") == "ɸaθer"
        # Voiced stop devoices but does NOT continue to fricativize; the
        # original voiceless k fricativizes
        assert PhoneticUtils.apply_sound_law("dekm", "grimm") == "texm"
        # Aspirate deaspirates but does NOT continue to devoice
        assert PhoneticUtils.apply_sound_law("bʰer", "grimm") == "ber"

    def test_apply_sound_law_grimm_s_cluster_blocked(self):
        """Voiceless stops after s are not shifted (sp, st, sk clusters)."""
        assert PhoneticUtils.apply_sound_law("sta", "grimm") == "sta"

    def test_apply_sound_law_rhotacism(self):
        """Intervocalic s becomes r."""
        assert PhoneticUtils.apply_sound_law("wesan", "rhotacism") == "weran"
        # Non-intervocalic s is untouched
        assert PhoneticUtils.apply_sound_law("stan", "rhotacism") == "stan"

    def test_apply_sound_law_final_devoicing(self):
        """Final voiced obstruents devoice."""
        assert PhoneticUtils.apply_sound_law("tag", "final_devoicing") == "tak"
        assert PhoneticUtils.apply_sound_law("hund", "final_devoicing") == "hunt"

    def test_apply_sound_law_unknown_raises(self):
        """Unknown law names raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Unknown sound law"):
            PhoneticUtils.apply_sound_law("word", "nonexistent_law")

    def test_known_sound_laws(self):
        """The law registry lists the documented laws."""
        laws = PhoneticUtils.known_sound_laws()
        assert "grimm" in laws
        assert "verner" in laws


class TestEmbeddingUtils:
    """Tests for EmbeddingUtils."""

    def test_cosine_similarity_identical(self):
        """Test cosine similarity for identical vectors."""
        vec = [1.0, 0.0, 0.0]
        result = EmbeddingUtils.cosine_similarity(vec, vec)
        assert abs(result - 1.0) < 0.0001

    def test_cosine_similarity_orthogonal(self):
        """Test cosine similarity for orthogonal vectors."""
        vec1 = [1.0, 0.0, 0.0]
        vec2 = [0.0, 1.0, 0.0]
        result = EmbeddingUtils.cosine_similarity(vec1, vec2)
        assert abs(result) < 0.0001

    def test_normalize(self):
        """Test vector normalization."""
        vec = [3.0, 4.0]
        result = EmbeddingUtils.normalize(vec)
        assert abs(result[0] - 0.6) < 0.0001
        assert abs(result[1] - 0.8) < 0.0001

    def test_average_embeddings(self):
        """Test embedding averaging."""
        embeddings = [[1.0, 2.0], [3.0, 4.0]]
        result = EmbeddingUtils.average_embeddings(embeddings)
        assert result == [2.0, 3.0]

    def test_average_embeddings_empty(self):
        """Test embedding averaging with empty input."""
        result = EmbeddingUtils.average_embeddings([])
        assert result == []

    def test_reduce_dimensions_shape(self):
        """PCA reduction returns target_dim coordinates per vector."""
        vectors = [[1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0], [1.0, 1.0, 1.0, 1.0]]
        result = EmbeddingUtils.reduce_dimensions(vectors, target_dim=2)
        assert len(result) == 3
        assert all(len(row) == 2 for row in result)

    def test_reduce_dimensions_preserves_structure(self):
        """Close vectors stay close, distant vectors stay distant after PCA."""
        import math

        a = [1.0, 0.0, 0.0, 0.0]
        a_close = [0.9, 0.1, 0.0, 0.0]
        b = [0.0, 0.0, 0.0, 1.0]
        reduced = EmbeddingUtils.reduce_dimensions([a, a_close, b], target_dim=2)

        def dist(u, v):
            return math.dist(u, v)

        assert dist(reduced[0], reduced[1]) < dist(reduced[0], reduced[2])

    def test_reduce_dimensions_not_all_zero(self):
        """Reduction of distinct vectors produces non-zero coordinates."""
        vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        result = EmbeddingUtils.reduce_dimensions(vectors, target_dim=2)
        assert any(any(abs(x) > 1e-9 for x in row) for row in result)

    def test_reduce_dimensions_empty(self):
        """Empty input returns empty output."""
        assert EmbeddingUtils.reduce_dimensions([]) == []

    def test_reduce_dimensions_single_vector(self):
        """A single vector reduces without error (centered to origin)."""
        result = EmbeddingUtils.reduce_dimensions([[1.0, 2.0, 3.0]], target_dim=2)
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_reduce_dimensions_unknown_method(self):
        """Unknown reduction methods raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="Unsupported reduction method"):
            EmbeddingUtils.reduce_dimensions([[1.0, 2.0]], method="tsne")

    def test_reduce_dimensions_mismatched_lengths(self):
        """Vectors of unequal length raise ValueError."""
        import pytest

        with pytest.raises(ValueError, match="same dimension"):
            EmbeddingUtils.reduce_dimensions([[1.0, 2.0], [1.0]])


class TestEmbeddingPipeline:
    """Tests for the EmbeddingPipeline encoder."""

    def _pipeline(self, dimension=64):
        from src.pipelines.embedding import EmbeddingPipeline

        return EmbeddingPipeline(dimension=dimension)

    def test_generate_embedding_deterministic(self):
        """Same text always produces the same vector."""
        p = self._pipeline()
        v1 = p.generate_embedding("a large body of water")
        v2 = p.generate_embedding("a large body of water")
        assert v1 == v2

    def test_generate_embedding_dimension_and_norm(self):
        """Vectors have the configured dimension and unit length."""
        import math

        p = self._pipeline(dimension=32)
        vec = p.generate_embedding("clear liquid essential for life")
        assert len(vec) == 32
        assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9

    def test_generate_embedding_empty_text(self):
        """Empty text yields a zero vector."""
        p = self._pipeline(dimension=16)
        assert p.generate_embedding("") == [0.0] * 16

    def test_similar_texts_are_closer(self):
        """Texts sharing vocabulary are closer than unrelated texts."""
        p = self._pipeline()
        water1 = p.generate_embedding("a clear liquid that falls as rain")
        water2 = p.generate_embedding("clear liquid found in rain and rivers")
        horse = p.generate_embedding("a large four-legged riding animal")

        sim_related = EmbeddingUtils.cosine_similarity(water1, water2)
        sim_unrelated = EmbeddingUtils.cosine_similarity(water1, horse)
        assert sim_related > sim_unrelated

    def test_time_slice_conditioning(self):
        """Same text in distant time slices differs, nearby years share a slice."""
        p = self._pipeline()
        base = p.generate_embedding("ruler of a kingdom", time_slice=1000)
        same_slice = p.generate_embedding("ruler of a kingdom", time_slice=1010)
        far_slice = p.generate_embedding("ruler of a kingdom", time_slice=1900)

        assert base == same_slice  # both fall in the same 50-year slice
        assert base != far_slice
        # Time conditioning is a minor component: still very similar
        assert EmbeddingUtils.cosine_similarity(base, far_slice) > 0.8

    def test_update_modified_and_full_retrain(self):
        """Pipeline populates semantic_vector on stored LSRs."""
        from src.models.lsr import LSR

        p = self._pipeline(dimension=16)
        lsr1 = LSR(
            form_orthographic="water",
            language_code="eng",
            language_name="English",
            definition_primary="clear liquid",
        )
        lsr2 = LSR(
            form_orthographic="mystery",
            language_code="eng",
            language_name="English",
            definition_primary="",  # nothing to embed
        )
        store = {lsr1.id: lsr1, lsr2.id: lsr2}
        p.set_lsr_store(store)

        result = p.update_modified([lsr1.id, lsr2.id])
        assert result == {"updated": 1, "skipped": 1, "failed": 0}
        assert len(lsr1.semantic_vector) == 16
        assert lsr2.semantic_vector == []

        retrain = p.full_retrain()
        assert retrain["processed"] == 1
        assert retrain["skipped"] == 1

    def test_update_modified_unknown_id_fails(self):
        """Unknown LSR IDs are counted as failed."""
        from uuid import uuid4

        p = self._pipeline(dimension=16)
        p.set_lsr_store({})
        result = p.update_modified([uuid4()])
        assert result["failed"] == 1

    def test_calculate_drift(self):
        """Drift is 0 without history, positive after the definition changes."""
        from src.models.lsr import LSR

        p = self._pipeline(dimension=64)
        lsr = LSR(
            form_orthographic="silly",
            language_code="eng",
            language_name="English",
            definition_primary="blessed and innocent",
        )
        store = {lsr.id: lsr}
        p.set_lsr_store(store)

        p.update_modified([lsr.id])
        assert p.calculate_drift(lsr.id) == 0.0  # no previous vector yet

        lsr.definition_primary = "foolish and lacking judgement"
        p.update_modified([lsr.id])
        assert p.calculate_drift(lsr.id) > 0.0
