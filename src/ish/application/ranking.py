"""Rank chunks for a query, fusing a vector order with a lexical one.

Every store scores the same way. A store supplies two primitives, the
vector order and the lexical order, and this module decides when to run
the lexical half, how wide a slice to fuse, and how to fuse it. Keeping
the policy here means a ranking experiment changes one place and every
store answers the same question the same way.

The lexical half runs only when the query names something. Measured on
this repository over 20 queries, fusing a lexical order into a plain
description cost 10 points of top-1 accuracy, because the vector order
is already the better signal there.
"""

import re
from collections.abc import Callable, Sequence

from ish.domain.chunk import Chunk
from ish.domain.match import Match

# Rank constant from the Reciprocal Rank Fusion paper. Large enough that
# no single list can dominate on its top hit alone.
RRF_K = 60

# Weights for the fused rankings. The vector ranking is the stronger
# signal on this kind of corpus, so it carries three times the lexical
# weight. Measured on this repository over 20 queries: equal weights cost
# 10 points of top-1 accuracy, and 3 to 1 costs none.
SEMANTIC_WEIGHT = 3.0
LEXICAL_WEIGHT = 1.0

# The smallest slice worth fusing. A chunk ranked well by the lexical half
# alone must still be able to surface, so both halves offer more than the
# page the caller asked for.
MIN_FUSION_WIDTH = 20

_WORD = re.compile(r"[A-Za-z0-9]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

ResultFilter = Callable[[Chunk], bool] | None
"""Say which chunks a search may return. None keeps everything."""

Semantic = Callable[[Sequence[float], int, ResultFilter], list[Match]]
"""Return the *top* chunks the filter allows, most similar first."""

Lexical = Callable[[str, int, ResultFilter], list[Chunk]]
"""Return the *top* chunks the filter allows, best lexical match first."""


def is_code_like(query: str) -> bool:
    """Return True when the query names something rather than describes it.

    Lexical matching earns its place only for a query that carries an
    identifier. Fusing it into a plain description costs accuracy,
    because the vector ranking is already the better signal there.
    """
    for token in _TOKEN.findall(query):
        if "_" in token:
            return True
        if len(token) > 2 and token.isupper():
            return True
        # Mixed case, such as IshApp or HTTPServer. An all-capital word is
        # already handled above, so a short one stays ordinary prose.
        if not token.isupper() and any(char.isupper() for char in token[1:]):
            return True
    return False


def split_identifier(name: str) -> str:
    """Split an identifier into the words it is built from.

    Turn ``PythonParser.parse`` into ``Python Parser parse`` so a lexical
    search matches a word inside a name, not only the whole name.
    """
    words: list[str] = []
    for part in _WORD.findall(name or ""):
        words.extend(_CAMEL.findall(part))
    return " ".join(words)


def fuse_rankings(
    rankings: Sequence[tuple[Sequence[Chunk], float]], limit: int
) -> list[Chunk]:
    """Merge weighted ranked lists with Reciprocal Rank Fusion.

    Score each chunk by ``weight / (RRF_K + rank)`` in every list it
    appears in. A chunk that both retrievers rank well beats one that
    only a single retriever loves, which is the point of running two.
    """
    scores: dict[Chunk, float] = {}
    for ranking, weight in rankings:
        for rank, chunk in enumerate(ranking, 1):
            scores[chunk] = scores.get(chunk, 0.0) + weight / (RRF_K + rank)

    ordered = sorted(scores, key=lambda chunk: -scores[chunk])
    return ordered[:limit]


def fusion_width(limit: int) -> int:
    """Return how many candidates each half offers to the fusion."""
    return max(limit * 4, MIN_FUSION_WIDTH)


def rank(
    query_vector: Sequence[float],
    query_text: str,
    limit: int,
    keep: ResultFilter,
    *,
    semantic: Semantic,
    lexical: Lexical,
) -> list[Match]:
    """Return the *limit* best matches for a query.

    Rank by vector similarity alone when *query_text* is empty or reads
    as prose. Otherwise fuse the vector order with the lexical order,
    which recovers an exact identifier a vector alone can miss.

    Both primitives apply *keep* before they count, so a filtered search
    returns a full page whenever the index holds one. A filter applied
    after the top slice starved a narrow filter: measured on one index,
    ``--type code`` returned nothing at a limit of 20 and two results at
    a limit of 100.

    Report the cosine similarity as the score whether or not the lexical
    half ran, so the number means the same thing either way.
    """
    if not (query_text and is_code_like(query_text)):
        return semantic(query_vector, limit, keep)

    width = fusion_width(limit)
    candidates = semantic(query_vector, width, keep)
    by_name = lexical(query_text, width, keep)
    if not by_name:
        return candidates[:limit]

    fused = fuse_rankings(
        [
            ([match.chunk for match in candidates], SEMANTIC_WEIGHT),
            (by_name, LEXICAL_WEIGHT),
        ],
        limit,
    )
    similarity = {match.chunk: match.score for match in candidates}
    return [Match(chunk, similarity.get(chunk, 0.0)) for chunk in fused]
