#!/usr/bin/env python3
"""Benchmark script for measuring system performance.

Runs real measurements:
- API latency: requests served in-process through the ASGI app (no server
  or network required), covering /health, LSR search, and text dating.
- Embedding generation: the hashed n-gram encoder over varied texts.
- Entity resolution: resolving entries against a populated store.
- Graph traversal: live Neo4j queries when the database is reachable
  (skipped otherwise).
"""

import asyncio
import sys
import time
from pathlib import Path
from statistics import mean, stdev
from typing import Any

# Add project root to path so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _latency_stats(latencies_ms: list[float]) -> dict[str, float]:
    ordered = sorted(latencies_ms)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return {
        "mean_ms": round(mean(ordered), 3),
        "stdev_ms": round(stdev(ordered), 3) if len(ordered) > 1 else 0.0,
        "min_ms": round(ordered[0], 3),
        "max_ms": round(ordered[-1], 3),
        "p95_ms": round(ordered[p95_index], 3),
    }


async def benchmark_api_latency(iterations: int = 50) -> list[dict[str, Any]]:
    """Benchmark API endpoints via in-process ASGI requests."""
    import httpx

    from src.api.main import app

    requests = [
        ("GET /health", "GET", "/health", None),
        ("GET /api/v1/lsr/search?form=water", "GET", "/api/v1/lsr/search?form=water", None),
        (
            "POST /api/v1/analyze/date-text",
            "POST",
            "/api/v1/analyze/date-text",
            {"text": "the knight rode forth to the castle", "language": "eng"},
        ),
    ]

    results = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
        for label, method, url, body in requests:
            print(f"Benchmarking {label} ({iterations} iterations)...")
            latencies = []
            statuses: set[int] = set()
            for _ in range(iterations):
                start = time.perf_counter()
                response = await client.request(method, url, json=body)
                latencies.append((time.perf_counter() - start) * 1000)
                statuses.add(response.status_code)
            results.append(
                {
                    "endpoint": label,
                    "iterations": iterations,
                    "status_codes": sorted(statuses),
                    **_latency_stats(latencies),
                }
            )
    return results


def benchmark_embedding_generation(count: int = 500) -> dict[str, Any]:
    """Benchmark embedding generation speed with the real encoder."""
    from src.pipelines.embedding import EmbeddingPipeline

    print(f"Benchmarking embedding generation ({count} embeddings)...")
    pipeline = EmbeddingPipeline()
    pipeline.load_model()

    texts = [
        f"definition number {i}: a {adj} {noun} used for {verb}ing"
        for i, (adj, noun, verb) in enumerate(
            (a, n, v)
            for a in ("large", "small", "ancient", "modern", "sacred")
            for n in ("vessel", "tool", "garment", "dwelling", "weapon")
            for v in ("carry", "cut", "cover", "shelter", "strik")
        )
    ][:count]
    # Repeat texts if count exceeds the generated variety
    while len(texts) < count:
        texts.append(texts[len(texts) % 125])

    start = time.perf_counter()
    for text in texts:
        pipeline.generate_embedding(text)
    elapsed = time.perf_counter() - start

    return {
        "operation": "embedding_generation",
        "count": count,
        "total_seconds": round(elapsed, 3),
        "per_second": round(count / elapsed, 1) if elapsed > 0 else float("inf"),
    }


def benchmark_entity_resolution(store_size: int = 500, lookups: int = 100) -> dict[str, Any]:
    """Benchmark entity resolution against a populated in-memory store."""
    from src.adapters.base import RawLexicalEntry
    from src.models.lsr import LSR
    from src.pipelines.entity_resolution import EntityResolver

    print(f"Benchmarking entity resolution ({store_size} LSRs, {lookups} lookups)...")
    store = {}
    for i in range(store_size):
        lsr = LSR(
            form_orthographic=f"word{i}",
            language_code="eng",
            language_name="English",
            definition_primary=f"definition of word {i}",
        )
        store[lsr.id] = lsr

    resolver = EntityResolver()
    resolver.set_lsr_store(store)

    start = time.perf_counter()
    for i in range(lookups):
        entry = RawLexicalEntry(
            source_id=f"bench-{i}",
            source_name="benchmark",
            form=f"word{i % store_size}",
            language="English",
            language_code="eng",
            definitions=[f"definition of word {i % store_size}"],
        )
        resolver.resolve(entry)
    elapsed = time.perf_counter() - start

    return {
        "operation": "entity_resolution",
        "store_size": store_size,
        "lookups": lookups,
        "total_seconds": round(elapsed, 3),
        "per_second": round(lookups / elapsed, 1) if elapsed > 0 else float("inf"),
    }


async def benchmark_graph_traversal(depth: int = 5, iterations: int = 20) -> dict[str, Any]:
    """Benchmark Neo4j graph traversal (skipped when Neo4j is unreachable)."""
    from src.utils.db import DatabaseManager

    print(f"Benchmarking graph traversal (depth {depth}, {iterations} iterations)...")
    db = DatabaseManager()
    if not await db.connect_neo4j():
        return {"operation": "graph_traversal", "skipped": "Neo4j unavailable"}

    query = f"""
    MATCH (start:LSR)
    WITH start LIMIT 1
    MATCH path = (start)-[:DESCENDS_FROM*0..{depth}]->(ancestor:LSR)
    RETURN count(path) AS paths
    """
    try:
        latencies = []
        for _ in range(iterations):
            start = time.perf_counter()
            async with db.neo4j_session() as session:
                result = await session.run(query)
                await result.single()
            latencies.append((time.perf_counter() - start) * 1000)
        return {
            "operation": "graph_traversal",
            "depth": depth,
            "iterations": iterations,
            **_latency_stats(latencies),
        }
    finally:
        await db.close_all()


async def main() -> list[dict[str, Any]]:
    """Run all benchmarks and print a summary."""
    print("Starting benchmarks...\n")
    results: list[dict[str, Any]] = []

    results.extend(await benchmark_api_latency())
    results.append(benchmark_embedding_generation())
    results.append(benchmark_entity_resolution())
    results.append(await benchmark_graph_traversal())

    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    for result in results:
        if "endpoint" in result:
            print(
                f"  {result['endpoint']}: mean {result['mean_ms']}ms, "
                f"p95 {result['p95_ms']}ms (status {result['status_codes']})"
            )
        elif result.get("skipped"):
            print(f"  {result['operation']}: skipped ({result['skipped']})")
        elif "per_second" in result:
            print(f"  {result['operation']}: {result['per_second']}/sec")
        else:
            print(f"  {result['operation']}: mean {result['mean_ms']}ms")
    print("=" * 60)
    return results


if __name__ == "__main__":
    asyncio.run(main())
