"""Performance tests for throughput and latency measurements.

These tests measure the performance of key operations to ensure
they meet performance requirements and to catch regressions.
"""

import pytest
import time
import statistics
from typing import Callable
from uuid import uuid4

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR, Attestation
from src.pipelines.entity_resolution import (
    EntityResolver,
    convert_entry_to_lsr,
)


def measure_execution_time(func: Callable, iterations: int = 100) -> dict:
    """Measure execution time statistics for a function."""
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append(end - start)

    return {
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "total": sum(times),
        "iterations": iterations,
        "ops_per_second": iterations / sum(times) if sum(times) > 0 else float('inf'),
    }


@pytest.mark.slow
class TestLSRCreationPerformance:
    """Test LSR creation performance."""

    def test_simple_lsr_creation_throughput(self):
        """Measure throughput of simple LSR creation."""
        def create_lsr():
            return LSR(
                form_orthographic="test",
                language_code="eng",
            )

        stats = measure_execution_time(create_lsr, iterations=1000)

        # Should be able to create at least 1000 LSRs per second
        assert stats["ops_per_second"] > 1000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nSimple LSR creation: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_complex_lsr_creation_throughput(self):
        """Measure throughput of complex LSR creation with all fields."""
        def create_complex_lsr():
            return LSR(
                form_orthographic="water",
                form_phonetic="ˈwɔːtər",
                language_code="eng",
                language_name="English",
                language_family="Indo-European",
                definition_primary="a colorless liquid essential for life",
                definitions_alternate=["H2O", "liquid", "body of water"],
                semantic_fields=["nature", "chemistry", "life"],
                part_of_speech=["noun"],
                date_start=1200,
                date_end=2024,
                source_databases=["wiktionary", "corpus", "clld"],
                attestations=[
                    Attestation(text_excerpt="sample 1", text_date=1300),
                    Attestation(text_excerpt="sample 2", text_date=1500),
                ],
            )

        stats = measure_execution_time(create_complex_lsr, iterations=500)

        # Should be able to create at least 500 complex LSRs per second
        assert stats["ops_per_second"] > 500, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nComplex LSR creation: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_lsr_normalization_throughput(self):
        """Measure throughput of LSR form normalization."""
        lsr = LSR(
            form_orthographic="Café",
            language_code="fra",
        )

        def normalize():
            lsr.normalize_form()

        stats = measure_execution_time(normalize, iterations=1000)

        # Normalization should be very fast
        assert stats["ops_per_second"] > 5000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nNormalization: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")


@pytest.mark.slow
class TestEntityResolutionPerformance:
    """Test entity resolution performance."""

    @pytest.fixture
    def resolver_with_data(self):
        """Create resolver with test data for performance tests."""
        resolver = EntityResolver(
            auto_merge_threshold=0.95,
            merge_with_flag_threshold=0.85,
            review_threshold=0.70,
        )

        # Pre-populate with 1000 entries
        lsrs = {}
        for i in range(1000):
            lsr = LSR(
                form_orthographic=f"word{i}",
                form_normalized=f"word{i}",
                language_code="eng",
                definition_primary=f"definition {i}",
                source_databases=["base"],
            )
            lsrs[lsr.id] = lsr

        resolver.set_lsr_store(lsrs)
        return resolver

    def test_resolution_throughput_no_match(self, resolver_with_data):
        """Measure resolution throughput when no match is found."""
        counter = [0]

        def resolve_new():
            entry = RawLexicalEntry(
                source_name="test",
                source_id=f"test-{counter[0]}",
                form=f"unique_word_{counter[0]}",
                language="English",
                language_code="eng",
            )
            counter[0] += 1
            return resolver_with_data.resolve(entry)

        stats = measure_execution_time(resolve_new, iterations=100)

        # Resolution with 1000-entry store - threshold based on measured performance
        # Note: Resolution scales linearly with store size due to similarity checking
        assert stats["ops_per_second"] > 10, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nResolution (no match): {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_resolution_throughput_with_match(self, resolver_with_data):
        """Measure resolution throughput when match is found."""
        def resolve_existing():
            entry = RawLexicalEntry(
                source_name="test",
                source_id="test-1",
                form="word500",  # Will match existing
                language="English",
                language_code="eng",
            )
            return resolver_with_data.resolve(entry)

        stats = measure_execution_time(resolve_existing, iterations=100)

        # Resolution with 1000-entry store - threshold based on measured performance
        assert stats["ops_per_second"] > 10, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nResolution (with match): {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_batch_processing_throughput(self, resolver_with_data):
        """Measure batch processing throughput."""
        entries = [
            RawLexicalEntry(
                source_name="test",
                source_id=f"test-{i}",
                form=f"batch_word_{i}",
                language="English",
                language_code="eng",
            )
            for i in range(100)
        ]

        def process_batch():
            return resolver_with_data.process_batch(entries)

        stats = measure_execution_time(process_batch, iterations=5)

        # Calculate entries per second
        entries_per_second = 100 * stats["ops_per_second"]

        # Batch processing with 1000-entry store - threshold based on measured performance
        assert entries_per_second > 20, f"Too slow: {entries_per_second:.0f} entries/sec"
        print(f"\nBatch processing: {entries_per_second:.0f} entries/sec, batch_time={stats['mean']*1000:.1f}ms")


@pytest.mark.slow
class TestConversionPerformance:
    """Test conversion performance."""

    def test_entry_to_lsr_conversion_throughput(self):
        """Measure entry to LSR conversion throughput."""
        entry = RawLexicalEntry(
            source_name="wiktionary",
            source_id="wikt-test-1",
            form="test",
            form_phonetic="/tɛst/",
            language="English",
            language_code="eng",
            definitions=["an examination", "a trial"],
            part_of_speech=["noun", "verb"],
            date_attested=1600,
        )

        def convert():
            return convert_entry_to_lsr(entry)

        stats = measure_execution_time(convert, iterations=500)

        # Should be able to convert at least 1000 entries per second
        assert stats["ops_per_second"] > 1000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nEntry to LSR conversion: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_lsr_to_graph_node_throughput(self):
        """Measure LSR to graph node conversion throughput."""
        lsr = LSR(
            form_orthographic="test",
            form_phonetic="/tɛst/",
            language_code="eng",
            language_name="English",
            definition_primary="a test",
            definitions_alternate=["examination"],
            source_databases=["wiktionary"],
        )

        def to_node():
            return lsr.to_graph_node()

        stats = measure_execution_time(to_node, iterations=1000)

        # Should be very fast
        assert stats["ops_per_second"] > 5000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nLSR to graph node: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_lsr_to_search_document_throughput(self):
        """Measure LSR to search document conversion throughput."""
        lsr = LSR(
            form_orthographic="test",
            form_phonetic="/tɛst/",
            language_code="eng",
            language_name="English",
            definition_primary="a test",
            definitions_alternate=["examination"],
            source_databases=["wiktionary"],
        )

        def to_doc():
            return lsr.to_search_document()

        stats = measure_execution_time(to_doc, iterations=1000)

        # Should be very fast
        assert stats["ops_per_second"] > 5000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nLSR to search doc: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")


@pytest.mark.slow
class TestMergePerformance:
    """Test merge operation performance."""

    def test_lsr_merge_throughput(self):
        """Measure LSR merge throughput."""
        def merge_pair():
            lsr1 = LSR(
                form_orthographic="test",
                language_code="eng",
                definition_primary="first definition",
                source_databases=["source1"],
            )
            lsr2 = LSR(
                form_orthographic="test",
                language_code="eng",
                definition_primary="second definition",
                source_databases=["source2"],
            )
            lsr1.merge_with(lsr2)
            return lsr1

        stats = measure_execution_time(merge_pair, iterations=500)

        # Should be able to merge at least 500 pairs per second
        assert stats["ops_per_second"] > 500, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nLSR merge: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")

    def test_merge_with_attestations_throughput(self):
        """Measure merge throughput with attestations."""
        def merge_with_attestations():
            lsr1 = LSR(
                form_orthographic="test",
                language_code="eng",
                source_databases=["source1"],
                attestations=[
                    Attestation(text_excerpt=f"text {i}", text_date=1500 + i)
                    for i in range(5)
                ],
            )
            lsr2 = LSR(
                form_orthographic="test",
                language_code="eng",
                source_databases=["source2"],
                attestations=[
                    Attestation(text_excerpt=f"other {i}", text_date=1600 + i)
                    for i in range(5)
                ],
            )
            lsr1.merge_with(lsr2)
            return lsr1

        stats = measure_execution_time(merge_with_attestations, iterations=200)

        # Should handle attestations reasonably fast
        assert stats["ops_per_second"] > 200, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nMerge with attestations: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.3f}ms")


@pytest.mark.slow
class TestValidationPerformance:
    """Test validation performance."""

    def test_sanitize_string_throughput(self):
        """Measure string sanitization throughput."""
        from src.utils.validation import sanitize_string

        test_string = "This is a test string with <html> tags and special chars: &amp; é ñ"

        def sanitize():
            return sanitize_string(test_string, max_length=1000)

        stats = measure_execution_time(sanitize, iterations=1000)

        # Should be very fast
        assert stats["ops_per_second"] > 10000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nString sanitization: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.4f}ms")

    def test_iso_code_sanitization_throughput(self):
        """Measure ISO code sanitization throughput."""
        from src.utils.validation import sanitize_iso_code

        def sanitize():
            return sanitize_iso_code("  ENG  ")

        stats = measure_execution_time(sanitize, iterations=1000)

        # Should be very fast
        assert stats["ops_per_second"] > 50000, f"Too slow: {stats['ops_per_second']:.0f} ops/sec"
        print(f"\nISO code sanitization: {stats['ops_per_second']:.0f} ops/sec, mean={stats['mean']*1000:.4f}ms")
