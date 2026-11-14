"""Search the tree of a server, and answer from it with a model of this machine.

``RemoteIsh`` is the session for a tree that another machine indexes.
The server searches and returns chunks. It runs no language model. This
machine runs the rerank, the answer, and each follow-up search, with the
same use case and the same model rules as an answer from a local tree.

    from ish.interfaces.python.remote import RemoteIsh

    remote = RemoteIsh(load_settings({"remote": "https://docs.example.com/mcp"}))
    print(remote.search("parse the config"))
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ish import bootstrap
from ish.application.answer import (
    Answer,
    AnswerBlock,
    AnswerCallback,
    AnswerQuestion,
)
from ish.application.candidates import candidates
from ish.application.glossary import Glossary
from ish.application.ports.language_model import LanguageModel
from ish.domain.chunk import Chunk
from ish.domain.match import Match
from ish.interfaces.python.api import ASK_LIMIT
from ish.settings import Settings

if TYPE_CHECKING:
    from ish.application.chat import Reading


log = logging.getLogger(__name__)


class RemoteIsh:
    """Search the tree of the server that ``remote`` names.

    The server searches its own tree under its own configuration. A
    filter travels only when a call gives it, because a filter this
    machine configures is for a tree of this machine.
    """

    def __init__(self, settings: Settings) -> None:
        """Read the server at ``settings.remote``. Send no request yet.

        Raise ``ChunkSourceError`` for a URL that is not http or https,
        and for a token that is not set.
        """
        self.settings = settings
        self._source = bootstrap.build_chunk_source(settings)
        self._models: dict[bool, LanguageModel] = {}
        self._built = False
        self._reranker: LanguageModel | None = None
        # What the acronyms of the tree stand for, as the server sent them.
        self._glossary = Glossary()
        # The text of each chunk the last answer read, so the next answer
        # of a chat can read it again without asking the server for it.
        self._texts: dict[Chunk, str] = {}

    @property
    def host(self) -> str:
        """Return the name of the server, for a line that says who answered."""
        return self._source.host

    @property
    def version(self) -> str:
        """Return the version of the server. Empty before the first call."""
        return self._source.version

    def search(
        self,
        query: str,
        limit: int | None = None,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
        shape: str = "",
    ) -> str:
        """Return the results of the server as lines, best first.

        The server reads ``lang:``, ``under:``, and ``type:`` out of
        *query*, and writes each path from the top of its tree. Write
        each line in *shape*, or in ``format`` when no shape is given.
        Return an empty text when nothing matches.
        """
        return self._source.search(
            query,
            limit if limit is not None else self.settings.limit,
            shape or self.settings.format,
            lang=lang,
            under=under,
            type=type,
        )

    def ask(
        self,
        question: str,
        *,
        lang: Sequence[str] = (),
        under: str = "",
        type: Sequence[str] = (),
        on_step: AnswerCallback | None = None,
        brief: bool = False,
        turns: int | None = None,
        reading: "Reading | None" = None,
        on_block: Callable[[AnswerBlock], None] | None = None,
    ) -> Answer:
        """Answer *question* from the chunks the server finds.

        Build the language model before any search, so a machine that
        allows no model reads no text of the server. Raise
        ``LanguageModelError`` when no model is set or allowed. Run the
        rerank and the answer here, and send each follow-up search to
        the server. The model reads the text the server sent, and no
        file of this machine. A chat gives a *reading*, and a terminal an
        *on_block*, as for ``Ish.ask``.
        """
        self.prepare(brief)
        limit = self.settings.rerank_candidates if self._reranker else ASK_LIMIT

        def narrowed(text: str, kinds: Sequence[str], count: int) -> list[Match]:
            found = self._source.sources(
                text, count, lang=lang, under=under, type=kinds or type
            )
            for entry in found.chunks:
                self._texts[entry.chunk] = entry.text
            for term in found.terms:
                self._glossary.keep(term)
            return [Match(entry.chunk, entry.score) for entry in found.chunks]

        def search(text: str) -> list[Match]:
            # As for a local answer: the best of each type for the rerank.
            if self._reranker is not None and not type:
                return candidates(narrowed, text, limit)
            return narrowed(text, (), limit)

        found, reranker = search, self._reranker
        if reading is not None:
            from ish.application.chat import plan

            found, reranker, turns = plan(reading, search, limit, reranker, turns)
        use_case = AnswerQuestion(
            search=found,
            model=self._models[brief],
            reranker=reranker,
            pick=ASK_LIMIT,
            turns=self.settings.ask_turns if turns is None else turns,
            read=self._texts.get,
            glossary=self._glossary,
            kind_of=self.kind_of,
            on_block=on_block,
            **({"on_step": on_step} if on_step else {}),
        )
        answer = use_case.answer(question)
        # Keep the texts of what this answer read, which a next turn of a
        # chat reads again, and no more: a server over MCP runs for days.
        read = set(answer.sources)
        self._texts = {c: t for c, t in self._texts.items() if c in read}
        return answer

    def query_for(self, question: str, history: str) -> str:
        """Return one search query for *question* of a chat, given its *history*.

        The rerank model writes it, the cheap one, or the model of the
        answer when no rerank model is set. Raise ``LanguageModelError``
        when no model is set or allowed, or when it does not reply.
        """
        self.prepare()
        model = self._reranker or self._models[False]
        query = model.query(question, history)
        log.info(
            "%s wrote the search query %r for %d tokens",
            model.model_name,
            query.text,
            query.input_tokens,
        )
        return query.text

    def prepare(self, brief: bool = False) -> None:
        """Build the language model of an answer, and the rerank model, now.

        Raise ``LanguageModelError`` when no model is set or allowed, so
        a machine that allows no model reads no text of the server.
        """
        if brief not in self._models:
            self._models[brief] = bootstrap.build_language_model(
                self.settings, brief=brief
            )
        if self._reranker is None and not self._built:
            self._reranker = bootstrap.build_reranker(self.settings)
            self._built = True

    def read(self, tool: str, arguments: Mapping[str, Any]) -> str:
        """Return what the read-only *tool* of the server answers with.

        *tool* is ``list_chunks``, ``index_status``, or ``complete_filter``.
        Only *arguments* travel, so no filter of this machine reaches the
        server.
        """
        return self._source.read(tool, arguments)

    def link(self, chunk: Chunk) -> str:
        """Return the URL the server gave for *chunk*."""
        return self._source.link(chunk)

    def kind_of(self, chunk: Chunk) -> str:
        """Return the type the server gave for *chunk*."""
        return self._source.kind_of(chunk)
