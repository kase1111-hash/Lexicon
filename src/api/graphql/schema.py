"""GraphQL schema definition using Strawberry.

Query fields resolve against the same repository and analysis modules as
the REST API; the database manager arrives via the request context set up
in src.api.main (GraphQLRouter context_getter).

Failures are reported in the response's `errors`, each with an
`extensions.code` (e.g. DATABASE_ERROR when the graph is unavailable);
fields that fail are null, while a missing LSR is null without an error.
Queries are limited in depth, aliases and the number of graph traversals
they can trigger (see _TraversalLimit).
"""

import logging
from collections.abc import Iterator
from typing import Any

import strawberry
from graphql import (
    FieldNode,
    FragmentSpreadNode,
    GraphQLError,
    InlineFragmentNode,
    IntValueNode,
    OperationDefinitionNode,
    SelectionSetNode,
    ValidationRule,
)
from strawberry.extensions import (
    AddValidationRules,
    MaxAliasesLimiter,
    QueryDepthLimiter,
    SchemaExtension,
)

from src.api.graphql import resolvers
from src.exceptions import (
    InvalidDateRangeError,
    InvalidLanguageCodeError,
    LexiconError,
    ValidationError,
)
from src.models.lsr import YEAR_MAX, YEAR_MIN
from src.repositories.lsr_repository import DEFAULT_ETYMOLOGY_DEPTH, MAX_LINEAGE_DEPTH
from src.utils.languages import language_name
from src.utils.validation import normalize_language_code

logger = logging.getLogger(__name__)

# Query limits: nesting depth (the README query has depth 2; etymology
# { steps { lsr { cognates { language { name } } } } } has 5), aliased fields
# per document, and graph traversals (LSR.ancestors/descendants/cognates
# resolutions, each a Cypher query) a query may trigger in the worst case.
MAX_QUERY_DEPTH = 5
MAX_ALIASES = 15
MAX_TRAVERSALS = 1000


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
    is_living: bool | None = strawberry.field(
        description=(
            "False for a reconstructed proto-language; null when unknown (the graph "
            "records no living/extinct status)"
        )
    )


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
    status: str = strawberry.field(
        description="ok, or insufficient_data when fewer than two dates can be compared"
    )
    explanation: str = strawberry.field(description="Why the status is what it is")


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
    proto_form: LSR | None = strawberry.field(
        description="The proto-form reached; null when the chain is truncated"
    )
    depth: int
    truncated: bool = strawberry.field(
        description="True when maxDepth cut off a line of ancestry (a deeper root may exist)"
    )


@strawberry.type
class DiagnosticWord:
    """A dated word that informed the date estimate."""

    form: str
    earliest_attestation: int | None
    date_label: str = strawberry.field(
        description="earliest_attestation as its source states it, e.g. 'c. 1220'"
    )
    last_attestation: int | None
    sets_bound: str | None


@strawberry.type
class DateAnalysis:
    """Result of text dating analysis."""

    predicted_range: list[int] | None
    confidence: float
    status: str
    explanation: str
    content_words: int
    dated_words: int
    diagnostic_vocabulary: list[DiagnosticWord]


@strawberry.type
class Anachronism:
    """A word that does not fit the claimed date."""

    form: str
    type: str
    earliest_attestation: int | None
    date_label: str = strawberry.field(
        description="earliest_attestation as its source states it (coined_after only)"
    )
    last_attestation: int | None
    gap_years: int
    severity: str


@strawberry.type
class AnachronismAnalysis:
    """Result of anachronism analysis."""

    anachronisms: list[Anachronism]
    verdict: str
    confidence: float
    explanation: str
    content_words: int
    dated_words: int


def _language_from_dict(data: dict[str, Any]) -> Language:
    code = data.get("iso_code") or data.get("language_code") or ""
    if "is_living" in data:
        living = data["is_living"]
    else:
        # The language of one LSR: one reconstructed form does not make its
        # language a proto-language (WOLD has reconstructed Hungarian forms),
        # and must not contradict Query.language for the same code
        living = resolvers.is_living(code, reconstructed=False)
    return Language(
        iso_code=code,
        name=data.get("name") or data.get("language_name") or language_name(code),
        family=data.get("family") or data.get("language_family"),
        branch_path=data.get("branch_path")
        or ([data["language_family"]] if data.get("language_family") else []),
        is_living=living,
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


def _language_arg(value: str) -> str:
    """Validate a language argument the way REST does ("en" -> "eng")."""
    try:
        return normalize_language_code(value)
    except ValueError as e:
        raise InvalidLanguageCodeError(language_code=value) from e


def _text_arg(text: str) -> str:
    """Apply REST's text limits: 10-100,000 characters."""
    if len(text) > 100_000:
        raise ValidationError(message="Text must be at most 100,000 characters", field="text")
    collapsed = " ".join(text.split())
    if len(collapsed) < 10:
        raise ValidationError(message="Text must be at least 10 characters", field="text")
    return collapsed


def _year_arg(year: int | None, field: str) -> int | None:
    """Apply the years an LSR can hold (-10000 to 2100), as REST searches do."""
    if year is not None and not YEAR_MIN <= year <= YEAR_MAX:
        raise ValidationError(
            message=f"{field} must be between {YEAR_MIN} and {YEAR_MAX}", field=field
        )
    return year


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
        if language:
            language = _language_arg(language)
        _year_arg(date_start, "dateStart")
        _year_arg(date_end, "dateEnd")
        if date_start is not None and date_end is not None and date_end < date_start:
            raise InvalidDateRangeError(start_date=date_start, end_date=date_end)
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
        """Get a language by code ("en" is looked up as "eng"; null if absent)."""
        # Also rejects an empty code, which would match every language
        results = await resolvers.resolve_languages(
            info.context["db"], iso_code=_language_arg(iso_code)
        )
        return _language_from_dict(results[0]) if results else None

    @strawberry.field
    async def languages(self, info: strawberry.Info, family: str | None = None) -> list[Language]:
        """Get all languages, optionally filtered by family."""
        results = await resolvers.resolve_languages(info.context["db"], family=family)
        return [_language_from_dict(r) for r in results]

    @strawberry.field
    async def etymology(
        self,
        info: strawberry.Info,
        lsr_id: strawberry.ID,
        max_depth: int = DEFAULT_ETYMOLOGY_DEPTH,
    ) -> EtymologyChain | None:
        """Get the etymology chain of an LSR back to its proto-form (null if no such LSR).

        Same rules as GET /api/v1/lsr/{id}/etymology: at most maxDepth
        (up to 50) DESCENDS_FROM steps along a shortest path.
        """
        data = await resolvers.resolve_etymology_chain(info.context["db"], str(lsr_id), max_depth)
        if data is None:
            return None
        steps = [
            EtymologyStep(lsr=_lsr_from_dict(step), depth=index)
            for index, step in enumerate(data["steps"])
        ]
        proto = data.get("proto_form")
        return EtymologyChain(
            steps=steps,
            proto_form=_lsr_from_dict(proto) if proto else None,
            depth=data["depth"],
            truncated=data["truncated"],
        )

    @strawberry.field
    async def semantic_trajectory(
        self, info: strawberry.Info, form: str, language: str
    ) -> SemanticTrajectory:
        """Get the semantic trajectory of a word over time."""
        data = await resolvers.resolve_semantic_trajectory(
            info.context["db"], form, _language_arg(language)
        )
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
            status=data.get("status", "ok"),
            explanation=data.get("explanation", ""),
        )

    @strawberry.field
    async def date_text(self, info: strawberry.Info, text: str, language: str) -> DateAnalysis:
        """Analyze text to predict its date."""
        data = await resolvers.resolve_date_text(
            info.context["db"], _text_arg(text), _language_arg(language)
        )
        return DateAnalysis(
            predicted_range=data["predicted_range"],
            confidence=data["confidence"],
            status=data["status"],
            explanation=data["explanation"],
            content_words=data["content_words"],
            dated_words=data["dated_words"],
            diagnostic_vocabulary=[DiagnosticWord(**w) for w in data["diagnostic_vocabulary"]],
        )

    @strawberry.field
    async def detect_anachronisms(
        self, info: strawberry.Info, text: str, claimed_date: int, language: str
    ) -> AnachronismAnalysis:
        """Detect anachronisms in text."""
        _year_arg(claimed_date, "claimedDate")
        data = await resolvers.resolve_detect_anachronisms(
            info.context["db"], _text_arg(text), claimed_date, _language_arg(language)
        )
        return AnachronismAnalysis(
            anachronisms=[Anachronism(**a) for a in data["anachronisms"]],
            verdict=data["verdict"],
            confidence=data["confidence"],
            explanation=data["explanation"],
            content_words=data["content_words"],
            dated_words=data["dated_words"],
        )


# Most LSRs each list field can return, for estimating a query's traversals.
# searchLsr's own `limit` argument (default 20) is used when given literally.
_LIST_SIZES = {
    "ancestors": resolvers.MAX_ANCESTORS,
    "descendants": resolvers.MAX_DESCENDANTS,
    "cognates": resolvers.MAX_COGNATES,
    "steps": MAX_LINEAGE_DEPTH + 1,
    "searchLsr": 100,
}
_TRAVERSAL_FIELDS = frozenset({"ancestors", "descendants", "cognates"})


class _TraversalLimit(ValidationRule):
    """Reject queries that could trigger more than MAX_TRAVERSALS graph traversals.

    Every LSR.ancestors/descendants/cognates field runs one Cypher query per
    parent LSR, so nesting them multiplies: `searchLsr(limit: 100)
    { descendants { cognates { form } } }` is shallow but may run 50,000
    queries. The estimate assumes every list field returns its maximum.
    """

    def enter_operation_definition(self, node: OperationDefinitionNode, *_args: Any) -> None:
        cost = self._cost(node.selection_set, 1, set())
        if cost > MAX_TRAVERSALS:
            self.report_error(
                GraphQLError(
                    f"Query may run up to {cost} graph traversals (limit {MAX_TRAVERSALS}); "
                    "nest fewer ancestors/descendants/cognates fields or lower searchLsr's limit",
                    nodes=[node],
                    extensions={"code": "QUERY_TOO_COMPLEX"},
                )
            )

    def _cost(self, selections: SelectionSetNode | None, multiplier: int, seen: set[str]) -> int:
        cost = 0
        for selection in selections.selections if selections else ():
            if isinstance(selection, FieldNode):
                name = selection.name.value
                if name in _TRAVERSAL_FIELDS:
                    cost += multiplier
                size = _LIST_SIZES.get(name, 1)
                if name == "searchLsr":
                    size = self._limit_argument(selection)
                cost += self._cost(selection.selection_set, multiplier * size, seen)
            elif isinstance(selection, InlineFragmentNode):
                cost += self._cost(selection.selection_set, multiplier, seen)
            elif isinstance(selection, FragmentSpreadNode):
                name = selection.name.value
                fragment = self.context.get_fragment(name)
                if fragment is not None and name not in seen:
                    cost += self._cost(fragment.selection_set, multiplier, seen | {name})
        return cost

    @staticmethod
    def _limit_argument(field: FieldNode) -> int:
        for argument in field.arguments:
            if argument.name.value == "limit":
                if isinstance(argument.value, IntValueNode):
                    return min(max(int(argument.value.value), 1), _LIST_SIZES["searchLsr"])
                return _LIST_SIZES["searchLsr"]  # a variable: assume the largest
        return 20


def _format_error(error: GraphQLError) -> GraphQLError:
    """Give application errors their code; hide unexpected ones."""
    original = error.original_error
    if original is None or isinstance(original, GraphQLError):
        return error  # syntax, validation and limit errors describe the query
    if isinstance(original, LexiconError):
        message, code = original.message, original.code
    else:
        logger.error(f"GraphQL resolver failed: {original!r}")
        message, code = "Internal server error", "INTERNAL_ERROR"
    return GraphQLError(
        message,
        nodes=error.nodes,
        source=error.source,
        positions=error.positions,
        path=error.path,
        original_error=original,
        extensions={"code": code},
    )


class _ErrorCodes(SchemaExtension):
    """Report LexiconError messages (already free of driver internals) with
    their code, and replace any other exception's message."""

    def on_operation(self) -> Iterator[None]:
        yield
        result = self.execution_context.result
        if result and result.errors:
            result.errors = [_format_error(error) for error in result.errors]


schema = strawberry.Schema(
    query=Query,
    extensions=[
        QueryDepthLimiter(max_depth=MAX_QUERY_DEPTH),
        MaxAliasesLimiter(max_alias_count=MAX_ALIASES),
        AddValidationRules([_TraversalLimit]),
        _ErrorCodes,
    ],
)
