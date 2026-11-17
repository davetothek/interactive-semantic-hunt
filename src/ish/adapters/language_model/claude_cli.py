"""Answer from sources with the Claude Code CLI, on the account it is logged in to.

The CLI has no citation feature, so the model replies in the JSON that
``_grounded`` fixes, through ``--json-schema``. It suits a person who
asks on their own machine. A shared server answers for many people, and
one login would carry them all, so a server uses the anthropic backend.

The model must read the sources and nothing else. The command turns off
every tool, every MCP server, every settings file, and every memory
file, and runs in an empty directory, so no hook, project file, or
memory reaches the model.
"""

import atexit
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ish.adapters.language_model import _grounded
from ish.adapters.language_model._calls import (
    DEFAULT_CLAUDE,
    DEFAULT_PROMPT_CACHE,
    DEFAULT_READ_USD,
    CallOptions,
    write_trace,
)
from ish.adapters.language_model._prompts import Prompts
from ish.adapters.language_model._prompts import load as load_prompts
from ish.application.ports.language_model import (
    Block,
    Claim,
    LanguageModelError,
    OnBlock,
    Query,
    Reply,
    Selection,
    Source,
    Verdicts,
)

log = logging.getLogger(__name__)

EXECUTABLE = DEFAULT_CLAUDE
TIMEOUT_SECONDS = 600

# Ask the CLI to load no memory: no CLAUDE.md of any scope, and no auto
# memory. Do not rely on these alone. Version 2.1.296 still loaded the
# auto memory of its working directory with both set, so the empty
# directory each call runs in is what keeps that memory out. A managed
# policy of an organization is loaded whatever is set, and only --bare
# skips it, which also skips the login.
# The tools an answer may use, all of them reads. Each is allowed only
# under the working directory, which is the tree: claude refuses a read
# of any other path in print mode, as it refuses every other tool.
READ_TOOLS = ("Read", "Grep", "Glob")

NO_MEMORY = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}

# The variables a call passes on, and no others. A server holds secrets
# in its environment, such as its MCP token and an API key, and a child
# process gets none of them. These are what the CLI needs to run, to
# find the login it is signed in with, and to reach the network through
# a proxy that a company may require.
PASSED_ON = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "CLAUDE_CONFIG_DIR",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
    "NODE_EXTRA_CA_CERTS",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)


# The requests of a call that reads no file. claude writes the reply that
# the schema fixes as a call of a tool, and the result of that tool takes
# one more request. A rerank, which has no tool to read with, makes two.
REPLY_REQUESTS = 2

# The variables that tell claude how long to keep a prompt in its cache.
# With none set, claude writes it for 1 hour.
CACHE_VARIABLES = {
    "5m": {"FORCE_PROMPT_CACHING_5M": "1"},
    "1h": {},
    "off": {"DISABLE_PROMPT_CACHING": "1"},
}


def environment(
    source: Mapping[str, str], prompt_cache: str = DEFAULT_PROMPT_CACHE
) -> dict[str, str]:
    """Return the environment of a call: the allowed names, no memory, the cache."""
    passed = {name: source[name] for name in PASSED_ON if name in source}
    return {**passed, **NO_MEMORY, **CACHE_VARIABLES[prompt_cache]}


def command(
    executable: str,
    model: str,
    system: str,
    schema: dict[str, object],
    *,
    stream: bool = False,
    tools: bool = False,
    read_usd: float = DEFAULT_READ_USD,
    effort: str = "",
    fallback_model: str = "",
    persist: bool = False,
    session: str = "",
    autocompact: int = 0,
) -> list[str]:
    """Return the argument list that asks one question with nothing else loaded.

    With *stream*, claude writes one JSON event a line as the reply grows,
    and the reply itself as the last event. With *tools*, claude may read
    the files of its working directory with Read, Grep, and Glob, and no
    others, and a call stops when it has cost *read_usd*. Ask for
    *effort*, and for *fallback_model* when the model is overloaded,
    only when each is set. With *persist*, claude keeps the session on
    disk, so a later call can resume it. With *session*, the call resumes
    that session. A session that is kept summarizes itself at
    *autocompact* tokens, when it is set.
    """
    allowed = (
        [
            "--tools",
            ",".join(READ_TOOLS),
            "--allowedTools",
            *(f"{tool}(./**)" for tool in READ_TOOLS),
            "--max-budget-usd",
            str(read_usd),
        ]
        if tools
        else ["--tools", ""]
    )
    output = (
        ["stream-json", "--verbose", "--include-partial-messages"]
        if stream
        else ["json"]
    )
    return [
        executable,
        "--print",
        "--setting-sources",
        "",
        *allowed,
        "--strict-mcp-config",
        *([] if persist or session else ["--no-session-persistence"]),
        *(["--resume", session] if session else []),
        *(
            ["--autocompact", str(autocompact)]
            if autocompact and (persist or session)
            else []
        ),
        "--model",
        model,
        *(["--effort", effort] if effort else []),
        *(["--fallback-model", fallback_model] if fallback_model else []),
        "--system-prompt",
        system,
        "--output-format",
        *output,
        "--json-schema",
        json.dumps(schema),
    ]


# Conversations a model keeps at most: an answer or a chat under way each.
MAX_CONVERSATIONS = 8


@dataclass(frozen=True, slots=True)
class Conversation:
    """Hold the session of the last call of a conversation, and what it holds.

    Attributes:
        session: The session that claude kept on disk.
        kind: The method that made the call. Each method has its own
            instructions, and a session keeps those it started with.
        sources: The title and the text of each source the session
            holds, by id.
        spent_usd: What claude says the session has cost so far. A
            resumed call reports the cost of its whole session.
    """

    session: str
    kind: str
    sources: Mapping[str, tuple[str, str]]
    spent_usd: float = 0.0


def unheld(
    sources: Sequence[Source], held: Mapping[str, tuple[str, str]]
) -> list[Source] | None:
    """Return the sources a session does not hold, in order.

    Return None when a source that the session holds under an id has
    another title or text now, because the model would read two texts
    under one id.
    """
    told = []
    for source in sources:
        kept = held.get(source.id)
        if kept is None:
            told.append(source)
        elif kept != (source.title, source.text):
            return None
    return told


def session_files(session: str, source: Mapping[str, str]) -> list[Path]:
    """Return the files where claude keeps *session*, under its config directory."""
    base = Path(source.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    return sorted((base / "projects").glob(f"*/{session}.jsonl"))


@dataclass(frozen=True, slots=True)
class Usage:
    """Hold what one call of claude read, wrote, and cost.

    Attributes:
        uncached: Input tokens that no cache held.
        written: Input tokens claude wrote to its prompt cache.
        cached: Input tokens claude read from its prompt cache.
        wrote: Output tokens, thinking included.
        cost_usd: What claude says the call cost at the prices of the API.
        requests: Requests to the model in the call. The reply that the
            schema fixes takes one of them.
    """

    uncached: int = 0
    written: int = 0
    cached: int = 0
    wrote: int = 0
    cost_usd: float = 0.0
    requests: int = 1

    @property
    def read(self) -> int:
        """Return every input token of the call, whatever it cost."""
        return self.uncached + self.written + self.cached


def usage_of(body: Mapping[str, object]) -> Usage:
    """Return the usage that the reply *body* of claude reports."""
    usage = body.get("usage")
    found = usage if isinstance(usage, Mapping) else {}

    def number(key: str) -> int:
        return int(found.get(key) or 0)

    return Usage(
        uncached=number("input_tokens"),
        written=number("cache_creation_input_tokens"),
        cached=number("cache_read_input_tokens"),
        wrote=number("output_tokens"),
        cost_usd=float(str(body.get("total_cost_usd") or 0.0)),
        requests=int(str(body.get("num_turns") or 1)),
    )


def run_whole(
    args: Sequence[str],
    prompt: str,
    *,
    cwd: str,
    env: Mapping[str, str],
    timeout: float,
) -> dict:
    """Run claude with *args* in *cwd*, and return the reply it writes at its end."""
    try:
        done = subprocess.run(
            list(args),
            input=prompt,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=dict(env),
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LanguageModelError(
            f"claude did not answer within {timeout:g} s."
        ) from exc
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        detail = (done.stderr or done.stdout).strip()[:500]
        raise LanguageModelError(
            f"claude exited {done.returncode} without a JSON reply: {detail}"
        ) from exc


class ClaudeCliLanguageModel:
    """Write answers with the ``claude`` command in print mode."""

    def __init__(
        self,
        model: str = "",
        *,
        prompts: Prompts | None = None,
        timeout: float = TIMEOUT_SECONDS,
        tree: Path | None = None,
        options: CallOptions | None = None,
    ) -> None:
        """Use *model*, a Claude model id or alias such as ``sonnet``.

        Require the model and the ``claude`` command on the path. With
        *tree*, an answer may read the files of that tree, and of no other
        place, before it replies. Every other call reads only its message.
        *options* set the cache, the cost of reading, and the trace.
        """
        if not model:
            raise LanguageModelError(
                "Set answer_model to a Claude model for the claude-cli backend."
            )
        self._options = options or CallOptions()
        found = shutil.which(self._options.claude)
        if found is None:
            raise LanguageModelError(
                f"The claude-cli backend needs the {self._options.claude!r} "
                "command on PATH."
            )
        self.model_name = model
        self._prompts = prompts or load_prompts()
        self._executable = found
        self._tree = tree
        # The rounds of reading of the last call: the requests of the call,
        # less the request that writes the reply and the one after it.
        self._reads = 0
        # The tokens of the last call that claude wrote to its prompt
        # cache, and those it read from it.
        self._cached = (0, 0)
        self._timeout = self._options.timeout or timeout
        # The session of the last call, and the conversation of each
        # question under way, which a later turn resumes.
        self._session = ""
        self._session_usd = 0.0
        self._conversations: dict[str, Conversation] = {}
        self._home: str | None = None
        # True when claude compacted the session of the last call, which
        # then holds a summary in place of the sources it was sent.
        self._compacted = False
        self._lock = threading.Lock()
        atexit.register(self.forget)

    def answer(
        self,
        question: str,
        sources: Sequence[Source],
        on_block: OnBlock | None = None,
        conversation: str = "",
    ) -> Reply:
        """Answer *question* from *sources* in the JSON the schema fixes.

        With *on_block*, stream the reply, and give on each block of it
        as soon as its JSON is whole. Resume the session of
        *conversation* as ``step`` does.
        """
        told, earlier = self._resumed(conversation, "answer", sources)
        persist, session, spent = self._session_of(conversation, earlier)
        structured, read, wrote, cost = self._run(
            "answer",
            self._prompts.answer,
            _grounded.SCHEMA,
            _grounded.prompt(question, told),
            on_block,
            reads=True,
            persist=persist,
            session=session,
            spent_usd=spent,
        )
        self._keep(conversation, "answer", told)
        reply = _grounded.parse(
            json.dumps(structured),
            f"claude {self.model_name}",
            input_tokens=read,
            output_tokens=wrote,
        )
        return self._with_usage(reply, cost)

    def step(
        self,
        question: str,
        sources: Sequence[Source],
        searched: Sequence[str],
        turns_left: int,
        on_block: OnBlock | None = None,
        notes: Sequence[str] = (),
        conversation: str = "",
    ) -> Reply:
        """Answer, or ask for searches, in the JSON the schema fixes.

        With ``resume``, a call of *conversation* resumes the session of
        the last call of it, and sends only the sources that session does
        not hold, so claude reads the rest from its cache.
        """
        told, earlier = self._resumed(conversation, "step", sources)
        persist, session, spent = self._session_of(conversation, earlier)
        structured, read, wrote, cost = self._run(
            "step",
            self._prompts.step,
            _grounded.STEP_SCHEMA,
            _grounded.step_prompt(question, told, searched, turns_left, notes),
            on_block,
            reads=True,
            persist=persist,
            session=session,
            spent_usd=spent,
        )
        self._keep(conversation, "step", told)
        reply = _grounded.parse_step(
            json.dumps(structured), f"claude {self.model_name}", read, wrote
        )
        return self._with_usage(reply, cost)

    def _resumed(
        self, conversation: str, kind: str, sources: Sequence[Source]
    ) -> tuple[Sequence[Source], Conversation | None]:
        """Return the sources a call sends, and the conversation it resumes.

        Resume nothing without ``resume`` or a conversation, after a call
        of another kind, or when a source the session holds has changed.
        """
        if not (self._options.resume and conversation):
            return sources, None
        with self._lock:
            found = self._conversations.get(conversation)
        if found is None or found.kind != kind:
            return sources, None
        told = unheld(sources, found.sources)
        if told is None:
            return sources, None
        return told, found

    def _session_of(
        self, conversation: str, earlier: Conversation | None
    ) -> tuple[bool, str, float]:
        """Return whether a call keeps its session, the one it resumes, and its cost.

        A call that resumes a session gives the session and what it cost
        so far. A call that starts one keeps it on disk.
        """
        if not (self._options.resume and conversation):
            return False, "", 0.0
        if earlier is None:
            return True, "", 0.0
        return False, earlier.session, earlier.spent_usd

    def _keep(self, conversation: str, kind: str, told: Sequence[Source]) -> None:
        """Keep the session of the last call as the state of *conversation*.

        The session holds what it held before and the sources *told*, the
        ones the call sent. A compaction leaves a summary of what it held
        before, so it then holds *told* alone. Delete a session that a new
        one takes the place of, and the oldest conversations past
        ``MAX_CONVERSATIONS``.
        """
        if not (self._options.resume and conversation):
            return
        sent = {s.id: (s.title, s.text) for s in told}
        with self._lock:
            before = self._conversations.pop(conversation, None)
            same = before is not None and before.session == self._session
            whole = before is not None and same and not self._compacted
            held = {**before.sources, **sent} if whole and before else sent
            if self._compacted:
                log.info("claude compacted the session, so ish sends its sources again")
            self._conversations[conversation] = Conversation(
                self._session, kind, held, self._session_usd
            )
            gone = [before] if before and not same else []
            while len(self._conversations) > MAX_CONVERSATIONS:
                gone.append(self._conversations.pop(next(iter(self._conversations))))
        for old in gone:
            self._delete(old.session)

    def forget(self) -> None:
        """Delete every session this model kept on disk, and its directory."""
        with self._lock:
            gone = list(self._conversations.values())
            self._conversations.clear()
            home, self._home = self._home, None
        for conversation in gone:
            self._delete(conversation.session)
        if home is not None:
            shutil.rmtree(home, ignore_errors=True)

    def _home_dir(self) -> str:
        """Return the empty directory where a call that reads no file keeps a session.

        claude finds a session by the directory it ran in, so a session
        that a later call resumes needs the same directory each time.
        """
        with self._lock:
            if self._home is None:
                self._home = tempfile.mkdtemp(prefix="ish-ask-")
            return self._home

    def _delete(self, session: str) -> None:
        """Delete the files of *session*, which hold the text of the tree."""
        for path in session_files(session, os.environ):
            path.unlink(missing_ok=True)

    def _with_usage(self, reply: Reply, cost: float) -> Reply:
        """Return *reply* with the cost, reads, and cache use of the last call."""
        written, read = self._cached
        return replace(
            reply,
            cost_usd=cost,
            reads=self._reads,
            cache_write_tokens=written,
            cache_read_tokens=read,
        )

    def check(self, claims: Sequence[Claim]) -> Verdicts:
        """Judge each claim against its quotes, in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            "check",
            self._prompts.check,
            _grounded.CHECK_SCHEMA,
            _grounded.check_prompt(claims),
        )
        verdicts = _grounded.parse_verdicts(
            json.dumps(structured), f"claude {self.model_name}", claims, read, wrote
        )
        return replace(verdicts, cost_usd=cost)

    def select(
        self, question: str, candidates: Sequence[Source], limit: int
    ) -> Selection:
        """Choose at most *limit* candidates, in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            "select",
            self._prompts.select,
            _grounded.SELECT_SCHEMA,
            _grounded.select_prompt(question, candidates, limit),
        )
        selection = _grounded.parse_ids(
            json.dumps(structured),
            f"claude {self.model_name}",
            limit,
            input_tokens=read,
            output_tokens=wrote,
        )
        return replace(selection, cost_usd=cost)

    def query(self, question: str, history: str) -> Query:
        """Write one search query, in the JSON the schema fixes."""
        structured, read, wrote, cost = self._run(
            "query",
            self._prompts.query,
            _grounded.QUERY_SCHEMA,
            _grounded.query_prompt(question, history),
        )
        query = _grounded.parse_query(
            json.dumps(structured), f"claude {self.model_name}", read, wrote
        )
        return replace(query, cost_usd=cost)

    def _run(
        self,
        kind: str,
        system: str,
        schema: dict[str, object],
        prompt: str,
        on_block: OnBlock | None = None,
        *,
        reads: bool = False,
        persist: bool = False,
        session: str = "",
        spent_usd: float = 0.0,
    ) -> tuple[object, int, int, float]:
        """Run one call, and return its structured output, tokens, and cost.

        The cost is what claude says the call would cost at the prices of
        the API, also when a subscription pays for it. With *on_block*,
        stream the reply and give on each block as it completes. With
        *reads* and a tree, run in the tree, and let claude read it.
        Otherwise run in an empty directory, with no tool. With a trace,
        write the call there under *kind*, the method that made it. With
        *persist*, keep the session on disk. With *session*, resume it,
        and take *spent_usd*, what the session cost before, off the cost
        claude reports, which is that of the whole session.
        """
        tools = reads and self._tree is not None
        if tools:
            system = f"{system}\n\n{self._prompts.tools}"
        started = time.perf_counter()
        self._compacted = False
        with tempfile.TemporaryDirectory(prefix="ish-ask-") as empty:
            kept = persist or bool(session)
            cwd = str(self._tree) if tools else (self._home_dir() if kept else empty)
            # claude says that it compacted a session only in its stream,
            # so a call that resumes one streams with no reader too.
            if on_block is None and not session:
                args = self._command(
                    kind, system, schema, tools=tools, persist=persist, session=session
                )
                body = self._whole(args, prompt, cwd)
            else:
                stream = _grounded.BlockStream(on_block or _unread)
                args = self._command(
                    kind,
                    system,
                    schema,
                    stream=True,
                    tools=tools,
                    persist=persist,
                    session=session,
                )
                body = self._streamed(args, prompt, cwd, stream.feed)
        failed = bool(body.get("is_error")) or "structured_output" not in body
        used = usage_of(body)
        self._session = str(body.get("session_id") or "")
        self._session_usd = used.cost_usd
        cost = max(0.0, used.cost_usd - spent_usd) if session else used.cost_usd
        if self._options.trace is not None:
            write_trace(
                self._options.trace,
                kind,
                {
                    "kind": kind,
                    "model": self.model_name,
                    "tools": tools,
                    "tree": str(self._tree) if tools else "",
                    "prompt_cache": self._options.prompt_cache,
                    "read_usd": self._options.read_usd,
                    "effort": self._options.effort_of(kind),
                    "fallback_model": self._options.fallback_model,
                    "resumed": session,
                    "autocompact": self._options.autocompact,
                    "compacted": self._compacted,
                    "system": system,
                    "schema": schema,
                    "prompt": prompt,
                    "seconds": round(time.perf_counter() - started, 3),
                    "usage": {
                        "uncached": used.uncached,
                        "written": used.written,
                        "cached": used.cached,
                        "wrote": used.wrote,
                    },
                    "requests": used.requests,
                    "cost_usd": cost,
                    "session_cost_usd": used.cost_usd,
                    "reply": body.get("structured_output"),
                    "error": str(body.get("result")) if failed else "",
                },
            )
        if failed:
            raise LanguageModelError(f"claude did not answer: {body.get('result')}")
        self._reads = max(0, used.requests - REPLY_REQUESTS) if tools else 0
        self._cached = (used.written, used.cached)
        log.info(
            "claude %s read %d tokens in %d requests: %d uncached, %d written to "
            "the cache, %d read from it. It wrote %d, for $%.4f",
            self.model_name,
            used.read,
            used.requests,
            used.uncached,
            used.written,
            used.cached,
            used.wrote,
            cost,
        )
        return body["structured_output"], used.read, used.wrote, cost

    def _whole(self, args: list[str], prompt: str, cwd: str) -> dict:
        """Run one call in *cwd*, and return the reply claude writes at its end."""
        return run_whole(
            args,
            prompt,
            cwd=cwd,
            env=environment(os.environ, self._options.prompt_cache),
            timeout=self._timeout,
        )

    def _command(
        self,
        kind: str,
        system: str,
        schema: dict[str, object],
        *,
        stream: bool = False,
        tools: bool = False,
        persist: bool = False,
        session: str = "",
    ) -> list[str]:
        """Return the arguments of one call of *kind*, under the options."""
        return command(
            self._executable,
            self.model_name,
            system,
            schema,
            stream=stream,
            tools=tools,
            read_usd=self._options.read_usd,
            effort=self._options.effort_of(kind),
            fallback_model=self._options.fallback_model,
            persist=persist,
            session=session,
            autocompact=self._options.autocompact,
        )

    def _streamed(
        self,
        args: list[str],
        prompt: str,
        cwd: str,
        on_json: Callable[[str], None],
    ) -> dict:
        """Run one call in *cwd* and stream it, and return the reply at its end.

        Give *on_json* each piece of the structured output as claude
        writes it. Stop claude when the call takes longer than the timeout
        or the reader stops it, so no process outlives the question.
        """
        late = threading.Event()
        with tempfile.TemporaryFile("w+") as errors:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=errors,
                text=True,
                cwd=cwd,
                env=environment(os.environ, self._options.prompt_cache),
            )
            timer = threading.Timer(self._timeout, _stop, (process, late))
            timer.start()
            body: dict | None = None
            try:
                assert process.stdin is not None and process.stdout is not None
                process.stdin.write(prompt)
                process.stdin.close()
                for line in process.stdout:
                    event = _event(line)
                    if _compacted(event):
                        self._compacted = True
                    if event.get("type") == "result":
                        body = event
                    elif piece := _structured_piece(event):
                        on_json(piece)
                process.wait()
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            if late.is_set():
                raise LanguageModelError(
                    f"claude did not answer within {self._timeout:g} s."
                )
            if body is None:
                errors.seek(0)
                detail = errors.read().strip()[:500]
                raise LanguageModelError(
                    f"claude exited {process.returncode} without a JSON reply: {detail}"
                )
        return body


def _stop(process: subprocess.Popen, late: threading.Event) -> None:
    """Stop *process*, which has run past its time, and say so."""
    late.set()
    process.kill()


def _event(line: str) -> dict:
    """Return one event of a stream, or an empty one for a line that is none."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return event if isinstance(event, dict) else {}


def _structured_piece(event: dict) -> str:
    """Return the piece of the structured output an event of a stream holds.

    claude writes the reply a schema fixes as the input of a tool call,
    and streams that input in pieces of JSON.
    """
    if event.get("type") != "stream_event":
        return ""
    delta = (event.get("event") or {}).get("delta") or {}
    if delta.get("type") != "input_json_delta":
        return ""
    return str(delta.get("partial_json") or "")


def _unread(block: Block) -> None:
    """Take a block of a stream that no one reads."""


def _compacted(event: dict) -> bool:
    """Return True for the event of a stream that says claude compacted the session."""
    return (
        event.get("type") == "system"
        and event.get("subtype") == "status"
        and event.get("compact_result") == "success"
    )
