"""GraphQL schema definition using Strawberry.

Query fields resolve against the same repository and analysis modules as
the REST API; the database manager arrives via the request context set up
in src.api.main (GraphQLRouter context_getter).
"""

from typing import Any

import strawberry

from src.api.graphql import resolvers


@strawberry.type
class Attestation:
    """A recorded usage of a word form."""

    text: str
    source: str
    date: int | None
    url: str | None


@strawberry.type
class Language:
    """A language in the system."""

    iso_code: str
    name: str
    family: str | None
    branch_path: list[str]
    is_living: bool


@strawberry.type
class SemanticField:
    """A semantic field/domain."""

    synset_id: str
    label: str
    domain: str | None


@strawberry.type
class TrajectoryPoint:
    """A point in a semantic trajectory."""

    date: int
    embedding_2d: list[float]
    definition: str | None
    attestation_count: int


@strawberry.type
class ShiftEvent:
    """A semantic shift event."""

    date: int
    change_type: str
    confidence: float
    before_meaning: str | None
    after_meaning: str | None


@strawberry.type
class SemanticTrajectory:
    """Semantic evolution trajectory."""

    points: list[TrajectoryPoint]
    shift_events: list[ShiftEvent]


@strawberry.type
class LSR:
    """Lexical State Record."""

    id: strawberry.ID
    form: str
    form_phonetic: str | None
    language: Language
    date_start: int | None
    date_end: int | None
    definitions: list[str]
    confidence: float
    is_reconstructed: bool
    attestations: list[Attestation]

    @strawberry.field
    async def ancestors(self, info: strawberry.Info, depth: int = 10) -> list["LSR"]:
        """Ancestor LSRs reached via DESCENDS_FROM edges."""
        db = info.context["db"]
        nodes = await resolvers.resolve_lsr_ancestors(db, str(self.id), depth)
        return [_lsr_from_dict(n) for n in nodes]

    @strawberry.field
    async def descendants(self, info: strawberry.Info, depth: int = 3) -> list["LSR"]:
        """Descendant LSRs reached via reverse DESCENDS_FROM edges."""
        db = info.context["db"]
        nodes = await resolvers.resolve_lsr_descendants(db, str(self.id), depth)
        return [_lsr_from_dict(n) for n in nodes]

    @strawberry.field
    async def cognates(self, info: strawberry.Info) -> list["LSR"]:
        """Cognates: other descendants of this LSR's proto-ancestor."""
        db = info.context["db"]
        nodes = await resolvers.resolve_lsr_cognates(db, str(self.id))
        return [_lsr_from_dict(n) for n in nodes]


@strawberry.type
class EtymologyStep:
    """One step in an etymology chain."""

    lsr: LSR
    depth: int


@strawberry.type
class EtymologyChain:
    """Full etymology chain from a form back to its proto-form."""

    steps: list[EtymologyStep]
    proto_form: LSR | None
    depth: int


@strawberry.type
class DiagnosticWord:
    """A word that contributed to date prediction."""

    form: str
    date_contribution: float
    earliest_attestation: int | None


@strawberry.type
class DateAnalysis:
    """Result of text dating analysis."""

    predicted_range: list[int]
    confidence: float
    diagnostic_vocabulary: list[DiagnosticWord]


@strawberry.type
class Anachronism:
    """A detected anachronism."""

    form: str
    earliest_attestation: int
    severity: str


@strawberry.type
class AnachronismAnalysis:
    """Result of anachronism analysis."""

    anachronisms: list[Anachronism]
    verdict: str


def _language_from_dict(data: dict[str, Any]) -> Language:
    return Language(
        iso_code=data.get("iso_code") or data.get("language_code") or "",
        name=data.get("name") or data.get("language_name") or "",
        family=data.get("family") or data.get("language_family"),
        branch_path=data.get("branch_path")
        or ([data["language_family"]] if data.get("language_family") else []),
        is_living=data.get("is_living", not data.get("reconstruction_flag", False)),
    )


def _lsr_from_dict(data: dict[str, Any]) -> LSR:
    definitions = data.get("definitions")
    if definitions is None:
        definitions = [data["definition"]] if data.get("definition") else []
    return LSR(
        id=strawberry.ID(str(data.get("id") or "")),
        form=data.get("form") or "",
        form_phonetic=data.get("form_phonetic") or None,
        language=_language_from_dict(data),
        date_start=data.get("date_start"),
        date_end=data.get("date_end"),
        definitions=definitions,
        confidence=data.get("confidence", 1.0),
        is_reconstructed=bool(data.get("reconstruction_flag", False)),
        attestations=[
            Attestation(
                text=a.get("text", ""),
                source=a.get("source", ""),
                date=a.get("date"),
                url=a.get("url"),
            )
            for a in data.get("attestations", [])
        ],
    )


@strawberry.type
class Query:
    """Root query type."""

    @strawberry.field
    async def lsr(self, info: strawberry.Info, id: strawberry.ID) -> LSR | None:
        """Get an LSR by ID."""
        data = await resolvers.resolve_lsr(info.context["db"], str(id))
        return _lsr_from_dict(data) if data else None

    @strawberry.field
    async def search_lsr(
        self,
        info: strawberry.Info,
        form: str | None = None,
        language: str | None = None,
        date_start: int | None = None,
        date_end: int | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[LSR]:
        """Search for LSRs."""
        results = await resolvers.resolve_search_lsr(
            info.context["db"],
            form=form,
            language=language,
            date_start=date_start,
            date_end=date_end,
            limit=limit,
            offset=offset,
        )
        return [_lsr_from_dict(r) for r in results]

    @strawberry.field
    async def language(self, info: strawberry.Info, iso_code: str) -> Language | None:
        """Get a language by ISO code."""
        results = await resolvers.resolve_languages(info.context["db"], iso_code=iso_code)
        return _language_from_dict(results[0]) if results else None

    @strawberry.field
    async def languages(self, info: strawberry.Info, family: str | None = None) -> list[Language]:
        """Get all languages, optionally filtered by family."""
        results = await resolvers.resolve_languages(info.context["db"], family=family)
        return [_language_from_dict(r) for r in results]

    @strawberry.field
    async def etymology(self, info: strawberry.Info, lsr_id: strawberry.ID) -> EtymologyChain:
        """Get the full etymology chain for an LSR."""
        data = await resolvers.resolve_etymology_chain(info.context["db"], str(lsr_id))
        steps = [
            EtymologyStep(lsr=_lsr_from_dict(step), depth=index)
            for index, step in enumerate(data["steps"])
        ]
        proto = data.get("proto_form")
        return EtymologyChain(
            steps=steps,
            proto_form=_lsr_from_dict(proto) if proto else None,
            depth=data.get("depth", 0),
        )

    @strawberry.field
    async def semantic_trajectory(
        self, info: strawberry.Info, form: str, language: str
    ) -> SemanticTrajectory:
        """Get the semantic trajectory of a word over time."""
        data = await resolvers.resolve_semantic_trajectory(info.context["db"], form, language)
        return SemanticTrajectory(
            points=[
                TrajectoryPoint(
                    date=p["date"],
                    embedding_2d=p["embedding_2d"],
                    definition=p["definition"],
                    attestation_count=p["attestation_count"],
                )
                for p in data["points"]
            ],
            shift_events=[
                ShiftEvent(
                    date=s["date"],
                    change_type=s["change_type"],
                    confidence=s["confidence"],
                    before_meaning=s["before_meaning"],
                    after_meaning=s["after_meaning"],
                )
                for s in data["shift_events"]
            ],
        )

    @strawberry.field
    async def date_text(self, info: strawberry.Info, text: str, language: str) -> DateAnalysis:
        """Analyze text to predict its date."""
        data = await resolvers.resolve_date_text(info.context["db"], text, language)
        return DateAnalysis(
            predicted_range=data["predicted_range"],
            confidence=data["confidence"],
            diagnostic_vocabulary=[
                DiagnosticWord(
                    form=w["form"],
                    date_contribution=w["date_contribution"],
                    earliest_attestation=w["earliest_attestation"],
                )
                for w in data["diagnostic_vocabulary"]
            ],
        )

    @strawberry.field
    async def detect_anachronisms(
        self, info: strawberry.Info, text: str, claimed_date: int, language: str
    ) -> AnachronismAnalysis:
        """Detect anachronisms in text."""
        data = await resolvers.resolve_detect_anachronisms(
            info.context["db"], text, claimed_date, language
        )
        return AnachronismAnalysis(
            anachronisms=[
                Anachronism(
                    form=a["form"],
                    earliest_attestation=a["earliest_attestation"],
                    severity=a["severity"],
                )
                for a in data["anachronisms"]
            ],
            verdict=data["verdict"],
        )


schema = strawberry.Schema(query=Query)
