"""Show an answer on a terminal as the model writes it, a block at a time.

Most of the wait for a full answer is the model writing it, 15 to 30 s
of it. A terminal shows the question and each block of the answer as
soon as the model has written it, numbered as the whole answer numbers
it, and the terms and the sources once the answer is whole. What the
terminal shows is the text the whole answer would be, so a copy of it
reads the same. A pipe gets the whole answer at its end, as before.
"""

from collections.abc import Callable

from ish.application.answer import Answer, AnswerBlock
from ish.domain.chunk import Chunk
from ish.interfaces.format import (
    answer_head,
    answer_tail,
    answer_text,
    block_lines,
    footnote,
    spaced,
)

# What a terminal shows when the whole answer does not start with the
# blocks it showed, such as after a follow-up search.
CHANGED = (
    "> The model searched again after the text above, and wrote the answer "
    "again. The whole answer follows."
)


class StreamedAnswer:
    """Show the blocks of one answer as they arrive, and then the rest of it."""

    def __init__(
        self,
        question: str,
        link: Callable[[Chunk], str],
        *,
        write: Callable[[str], None],
        render: Callable[[str], str] | None = None,
        footnotes: bool = True,
    ) -> None:
        """Write each block through *write*, drawn by *render* when it is given.

        Mark citations as footnotes, or with *footnotes* False as ``[n]``,
        as a chat does.
        """
        self._question = question
        self._link = link
        self._write = write
        self._render = render
        self._footnotes = footnotes
        self._mark = footnote if footnotes else spaced
        self._numbers: dict[Chunk, int] = {}
        self._shown: list[AnswerBlock] = []
        # The sources of the answer in the order of their numbers, once
        # ``rest`` has written its end.
        self.cited: list[Chunk] = []

    @property
    def shown(self) -> bool:
        """Return True once a block of the answer has been shown."""
        return bool(self._shown)

    def block(self, block: AnswerBlock) -> None:
        """Show *block*, after the heading when it is the first one."""
        if not self._shown:
            self._show(answer_head(self._question))
        self._show(block_lines(block, self._numbers, self._mark))
        self._shown.append(block)

    def rest(
        self, answer: Answer, kind_of: Callable[[Chunk], str] | None = None
    ) -> str:
        """Return the Markdown of *answer* that has to follow what was shown.

        Return the whole answer when nothing was shown. When the answer
        does not start with the blocks shown, say so, and return the whole
        answer after that.
        """
        if not self._shown:
            return self._whole(answer, kind_of)
        shown = len(self._shown)
        if not (answer.covered and list(answer.blocks[:shown]) == self._shown):
            return f"{CHANGED}\n\n{self._whole(answer, kind_of)}"
        lines: list[str] = []
        for block in answer.blocks[shown:]:
            lines += block_lines(block, self._numbers, self._mark)
        tail, self.cited = answer_tail(
            answer,
            self._numbers,
            self._link,
            kind_of=kind_of,
            footnotes=self._footnotes,
        )
        return "\n".join(lines + tail)

    def _whole(self, answer: Answer, kind_of: Callable[[Chunk], str] | None) -> str:
        text, self.cited = answer_text(
            answer,
            self._question,
            self._link,
            kind_of=kind_of,
            footnotes=self._footnotes,
        )
        return text

    def _show(self, lines: list[str]) -> None:
        """Write *lines* as one piece of the answer, drawn when a renderer is set."""
        text = "\n".join(lines)
        self._write(f"{self._render(text) if self._render else text}\n")
