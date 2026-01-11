"""Stress tests for system stability under load.

These tests verify the system remains stable and performs
acceptably under heavy load and with large datasets.
"""

import pytest
import time
import gc
from typing import List

from src.adapters.base import RawLexicalEntry
from src.models.lsr import LSR, Attestation
from src.pipelines.entity_resolution import EntityResolver, convert_entry_to_lsr


def get_memory_usage_mb() -> float:
    """Get current process memory usage in MB."""
    import os
    try:
        # Try to read from /proc on Linux
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # Convert KB to MB
    except (FileNotFoundError, IOError):
        pass

    # Fallback: estimate from object count
    return 0


@pytest.mark.slow
class TestLargeDatasetHandling:
    """Test handling of large datasets."""

    def test_create_large_lsr_batch(self):
        """Test creating a large batch of LSRs."""
        batch_size = 5000
        start_time = time.perf_counter()

        lsrs = []
        for i in range(batch_size):
            lsr = LSR(
                form_orthographic=f"word{i}",
                language_code="eng",
                definition_primary=f"definition for word {i}",
                source_databases=["test"],
            )
            lsrs.append(lsr)

        elapsed = time.perf_counter() - start_time
        rate = batch_size / elapsed

        print(f"\nCreated {batch_size} LSRs in {elapsed:.2f}s ({rate:.0f}/sec)")

        # Should create at least 1000 LSRs per second
        assert rate > 1000, f"Too slow: {rate:.0f}/sec"
        assert len(lsrs) == batch_size

    def test_resolver_with_large_store(self):
        """Test resolver performance with large existing store."""
        store_size = 10000
        resolver = EntityResolver()

        # Build large store
        print(f"\nBuilding store with {store_size} entries...")
        start_time = time.perf_counter()

        lsrs = {}
        for i in range(store_size):
            lsr = LSR(
                form_orthographic=f"existing{i}",
                form_normalized=f"existing{i}",
                language_code="eng",
                definition_primary=f"definition {i}",
                source_databases=["base"],
            )
            lsrs[lsr.id] = lsr

        resolver.set_lsr_store(lsrs)
        build_time = time.perf_counter() - start_time
        print(f"Store built in {build_time:.2f}s")

        # Test resolution against large store
        query_count = 100
        start_time = time.perf_counter()

        for i in range(query_count):
            entry = RawLexicalEntry(
                source_name="test",
                source_id=f"test-{i}",
                form=f"query{i}",
                language="English",
                language_code="eng",
            )
            resolver.resolve(entry)

        query_time = time.perf_counter() - start_time
        query_rate = query_count / query_time

        print(f"Resolved {query_count} queries in {query_time:.2f}s ({query_rate:.0f}/sec)")

        # With 10k entries, resolution is slower due to O(n) similarity checking
        # This documents the expected scaling behavior
        assert query_rate > 1, f"Too slow with large store: {query_rate:.0f}/sec"

    def test_batch_processing_large_batch(self):
        """Test batch processing with large batch size."""
        batch_size = 1000
        resolver = EntityResolver()

        # Pre-populate store
        lsrs = {}
        for i in range(1000):
            lsr = LSR(
                form_orthographic=f"existing{i}",
                form_normalized=f"existing{i}",
                language_code="eng",
                source_databases=["base"],
            )
            lsrs[lsr.id] = lsr
        resolver.set_lsr_store(lsrs)

        # Create large batch
        entries = [
            RawLexicalEntry(
                source_name="test",
                source_id=f"test-{i}",
                form=f"batch{i}",
                language="English",
                language_code="eng",
            )
            for i in range(batch_size)
        ]

        start_time = time.perf_counter()
        results = resolver.process_batch(entries)
        elapsed = time.perf_counter() - start_time
        rate = batch_size / elapsed

        print(f"\nProcessed batch of {batch_size} in {elapsed:.2f}s ({rate:.0f}/sec)")

        assert len(results) == batch_size
        # With 1000-entry store, batch processing is limited by O(n) resolution
        assert rate > 20, f"Batch processing too slow: {rate:.0f}/sec"


@pytest.mark.slow
class TestMemoryUsage:
    """Test memory usage patterns."""

    def test_no_memory_leak_on_lsr_creation(self):
        """Test that LSR creation doesn't leak memory."""
        gc.collect()
        initial_objects = len(gc.get_objects())

        # Create and discard many LSRs
        for batch in range(10):
            lsrs = []
            for i in range(1000):
                lsr = LSR(
                    form_orthographic=f"word{i}",
                    language_code="eng",
                )
                lsrs.append(lsr)
            # Discard
            lsrs.clear()
            gc.collect()

        final_objects = len(gc.get_objects())
        object_growth = final_objects - initial_objects

        print(f"\nObject count: initial={initial_objects}, final={final_objects}, growth={object_growth}")

        # Allow some growth for caches, but not excessive
        assert object_growth < 10000, f"Potential memory leak: {object_growth} object growth"

    def test_no_memory_leak_on_resolution(self):
        """Test that resolution doesn't leak memory."""
        resolver = EntityResolver()

        gc.collect()
        initial_objects = len(gc.get_objects())

        # Perform many resolutions
        for batch in range(10):
            for i in range(100):
                entry = RawLexicalEntry(
                    source_name="test",
                    source_id=f"test-{batch}-{i}",
                    form=f"word{i}",
                    language="English",
                    language_code="eng",
                )
                resolver.resolve(entry)
            gc.collect()

        final_objects = len(gc.get_objects())
        object_growth = final_objects - initial_objects

        print(f"\nObject count after resolutions: growth={object_growth}")

        # Allow some growth but not excessive
        assert object_growth < 10000, f"Potential memory leak in resolution: {object_growth}"


@pytest.mark.slow
class TestConcurrentLoad:
    """Test behavior under concurrent-like load patterns."""

    def test_rapid_sequential_operations(self):
        """Test rapid sequential operations (simulating concurrent load)."""
        resolver = EntityResolver()

        # Pre-populate
        lsrs = {}
        for i in range(500):
            lsr = LSR(
                form_orthographic=f"word{i}",
                form_normalized=f"word{i}",
                language_code="eng",
                source_databases=["base"],
            )
            lsrs[lsr.id] = lsr
        resolver.set_lsr_store(lsrs)

        # Simulate rapid requests
        operation_count = 1000
        start_time = time.perf_counter()

        for i in range(operation_count):
            # Mix of operations
            if i % 3 == 0:
                # Resolution
                entry = RawLexicalEntry(
                    source_name="test",
                    source_id=f"test-{i}",
                    form=f"query{i % 100}",
                    language="English",
                    language_code="eng",
                )
                resolver.resolve(entry)
            elif i % 3 == 1:
                # LSR creation
                LSR(
                    form_orthographic=f"new{i}",
                    language_code="eng",
                )
            else:
                # Conversion
                entry = RawLexicalEntry(
                    source_name="test",
                    source_id=f"conv-{i}",
                    form=f"convert{i}",
                    language="English",
                    language_code="eng",
                )
                convert_entry_to_lsr(entry)

        elapsed = time.perf_counter() - start_time
        rate = operation_count / elapsed

        print(f"\nMixed operations: {operation_count} ops in {elapsed:.2f}s ({rate:.0f}/sec)")

        # Should handle mixed load efficiently (includes resolution which is slower)
        assert rate > 200, f"Mixed operations too slow: {rate:.0f}/sec"

    def test_burst_load(self):
        """Test handling of burst load."""
        resolver = EntityResolver()

        burst_sizes = [100, 500, 1000, 500, 100]
        total_ops = 0
        start_time = time.perf_counter()

        for burst_size in burst_sizes:
            entries = [
                RawLexicalEntry(
                    source_name="burst",
                    source_id=f"burst-{i}",
                    form=f"burst{i}",
                    language="English",
                    language_code="eng",
                )
                for i in range(burst_size)
            ]
            resolver.process_batch(entries)
            total_ops += burst_size

        elapsed = time.perf_counter() - start_time
        rate = total_ops / elapsed

        print(f"\nBurst load: {total_ops} ops in {elapsed:.2f}s ({rate:.0f}/sec)")

        # Should handle burst load
        assert rate > 300, f"Burst load handling too slow: {rate:.0f}/sec"


@pytest.mark.slow
class TestLargeAttestationHandling:
    """Test handling of LSRs with many attestations."""

    def test_lsr_with_many_attestations(self):
        """Test LSR creation with many attestations."""
        attestation_count = 100

        start_time = time.perf_counter()

        for _ in range(50):
            lsr = LSR(
                form_orthographic="test",
                language_code="eng",
                attestations=[
                    Attestation(
                        text_excerpt=f"attestation {i} with some longer text content",
                        text_date=1500 + i,
                    )
                    for i in range(attestation_count)
                ],
            )

        elapsed = time.perf_counter() - start_time

        print(f"\n50 LSRs with {attestation_count} attestations each: {elapsed:.2f}s")

        # Should be reasonably fast
        assert elapsed < 5.0, f"Too slow handling many attestations: {elapsed:.2f}s"

    def test_merge_with_many_attestations(self):
        """Test merging LSRs with many attestations."""
        def create_lsr_with_attestations(count: int, prefix: str) -> LSR:
            return LSR(
                form_orthographic="test",
                language_code="eng",
                source_databases=[prefix],
                attestations=[
                    Attestation(
                        text_excerpt=f"{prefix} attestation {i}",
                        text_date=1500 + i,
                    )
                    for i in range(count)
                ],
            )

        merge_count = 20
        start_time = time.perf_counter()

        for i in range(merge_count):
            lsr1 = create_lsr_with_attestations(50, f"source{i}a")
            lsr2 = create_lsr_with_attestations(50, f"source{i}b")
            lsr1.merge_with(lsr2)

        elapsed = time.perf_counter() - start_time

        print(f"\n{merge_count} merges with 50+50 attestations: {elapsed:.2f}s")

        # Should handle merges reasonably fast
        assert elapsed < 5.0, f"Merge with attestations too slow: {elapsed:.2f}s"


@pytest.mark.slow
class TestScalabilityLimits:
    """Test scalability limits and degradation."""

    def test_increasing_store_size_impact(self):
        """Test how resolution time scales with store size."""
        sizes = [100, 500, 1000, 2000, 5000]
        times_per_query = []

        for size in sizes:
            resolver = EntityResolver()

            # Build store of given size
            lsrs = {}
            for i in range(size):
                lsr = LSR(
                    form_orthographic=f"word{i}",
                    form_normalized=f"word{i}",
                    language_code="eng",
                    source_databases=["base"],
                )
                lsrs[lsr.id] = lsr
            resolver.set_lsr_store(lsrs)

            # Measure query time
            query_count = 50
            start_time = time.perf_counter()

            for j in range(query_count):
                entry = RawLexicalEntry(
                    source_name="test",
                    source_id=f"test-{j}",
                    form=f"query{j}",
                    language="English",
                    language_code="eng",
                )
                resolver.resolve(entry)

            elapsed = time.perf_counter() - start_time
            avg_time = elapsed / query_count
            times_per_query.append(avg_time)

            print(f"Store size {size}: {avg_time*1000:.2f}ms/query")

        # Check that scaling is reasonable (not worse than O(n^2))
        # Time at 5000 should not be more than 100x time at 100
        scaling_factor = times_per_query[-1] / times_per_query[0] if times_per_query[0] > 0 else 0
        print(f"\nScaling factor (5000 vs 100): {scaling_factor:.1f}x")

        # Allow some scaling but not excessive
        assert scaling_factor < 100, f"Poor scaling: {scaling_factor:.1f}x slowdown"

    def test_deep_definition_lists(self):
        """Test handling of LSRs with many definitions."""
        definition_counts = [1, 10, 50, 100]
        creation_times = []

        for count in definition_counts:
            iterations = 100

            start_time = time.perf_counter()
            for i in range(iterations):
                LSR(
                    form_orthographic="test",
                    language_code="eng",
                    definition_primary="primary definition",
                    definitions_alternate=[f"definition {j}" for j in range(count)],
                )
            elapsed = time.perf_counter() - start_time

            avg_time = elapsed / iterations
            creation_times.append(avg_time)
            print(f"LSR with {count} definitions: {avg_time*1000:.3f}ms")

        # Should scale reasonably
        scaling_factor = creation_times[-1] / creation_times[0] if creation_times[0] > 0 else 0
        print(f"\nScaling factor (100 vs 1 definitions): {scaling_factor:.1f}x")

        assert scaling_factor < 50, f"Poor definition scaling: {scaling_factor:.1f}x"
