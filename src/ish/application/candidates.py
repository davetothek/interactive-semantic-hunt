"""Choose the candidates of an answer: the best overall, and the best of each type.

One search ranks every chunk on one scale, and a question in prose lands
nearest to prose. So a rerank model that judges the top of one search
never sees the code that does what a specification says. Search once for
each type as well, and keep the best few of each type beside the best
overall.
"""

from collections.abc import Callable, Sequence

from ish.application.categories import CODE, DOC, TEST
from ish.domain.chunk import Chunk
from ish.domain.match import Match

# The types an answer reads from. Configuration seldom explains anything,
# so it gets no place of its own and competes in the overall ranking.
KINDS = (DOC, TEST, CODE)

# Return the best of a query, as many as asked, narrowed to the types
# given, or to none for every type.
KindSearch = Callable[[str, Sequence[str], int], Sequence[Match]]


def candidates(search: KindSearch, text: str, limit: int) -> list[Match]:
    """Return at most *limit* candidates for *text*, with each type in them.

    Keep the best of each type of ``KINDS``, a fifth of *limit* each,
    then fill the rest with the best overall. Return them by score, best
    first. The searches read one index with one model, so their scores
    compare.
    """
    overall = search(text, (), limit)
    floor = max(1, limit // 5)
    kept: dict[Chunk, float] = {}
    for kind in KINDS:
        for chunk, score in search(text, (kind,), floor):
            kept.setdefault(chunk, score)
    for chunk, score in overall:
        if len(kept) >= limit:
            break
        kept.setdefault(chunk, score)
    ranked = sorted(kept.items(), key=lambda pair: pair[1], reverse=True)
    return [Match(chunk, score) for chunk, score in ranked[:limit]]
