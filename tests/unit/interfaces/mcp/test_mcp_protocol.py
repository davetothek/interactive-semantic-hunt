"""Test the stdio JSON-RPC layer of the MCP interface."""

import io
import json

from ish.interfaces.mcp.protocol import (
    DEFAULT_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Server,
    Tool,
)

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}}


def echo_tool(name: str = "echo") -> Tool:
    return Tool(
        name=name,
        description="Echo the argument back.",
        schema=SCHEMA,
        handler=lambda args: f"got {args.get('x')}",
    )


def build(*tools: Tool) -> Server:
    return Server("ish", "1.2.3", list(tools) or [echo_tool()])


def call(server: Server, method: str, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


class TestInitialize:
    """Verify the handshake."""

    def test_reports_server_identity(self) -> None:
        result = call(build(), "initialize", {"protocolVersion": DEFAULT_VERSION})
        assert result["result"]["serverInfo"] == {"name": "ish", "version": "1.2.3"}

    def test_echoes_a_supported_version(self) -> None:
        result = call(build(), "initialize", {"protocolVersion": "2024-11-05"})
        assert result["result"]["protocolVersion"] == "2024-11-05"

    def test_falls_back_for_an_unknown_version(self) -> None:
        """A host asking for a revision we do not know still gets a server."""
        result = call(build(), "initialize", {"protocolVersion": "1999-01-01"})
        assert result["result"]["protocolVersion"] == DEFAULT_VERSION

    def test_declares_tool_capability(self) -> None:
        result = call(build(), "initialize", {})
        assert "tools" in result["result"]["capabilities"]


class TestToolListing:
    """Verify discovery."""

    def test_lists_every_tool(self) -> None:
        result = call(build(echo_tool("a"), echo_tool("b")), "tools/list")
        assert [t["name"] for t in result["result"]["tools"]] == ["a", "b"]

    def test_entry_carries_schema_and_description(self) -> None:
        entry = call(build(), "tools/list")["result"]["tools"][0]
        assert entry["inputSchema"] == SCHEMA
        assert entry["description"]


class TestToolCall:
    """Verify invocation and failure reporting."""

    def test_runs_the_handler(self) -> None:
        result = call(build(), "tools/call", {"name": "echo", "arguments": {"x": "hi"}})
        assert result["result"]["content"] == [{"type": "text", "text": "got hi"}]
        assert result["result"]["isError"] is False

    def test_missing_arguments_default_to_empty(self) -> None:
        result = call(build(), "tools/call", {"name": "echo"})
        assert result["result"]["isError"] is False

    def test_unknown_tool_is_a_tool_error(self) -> None:
        """The host should show the model why, not fail the call."""
        result = call(build(), "tools/call", {"name": "nope"})
        assert result["result"]["isError"] is True
        assert "Unknown tool" in result["result"]["content"][0]["text"]

    def test_handler_failure_is_a_tool_error(self) -> None:
        def boom(args):
            raise ValueError("the query is required")

        failing = Tool("bad", "Fails.", SCHEMA, boom)
        result = call(build(failing), "tools/call", {"name": "bad"})

        assert result["result"]["isError"] is True
        assert "the query is required" in result["result"]["content"][0]["text"]


class TestProtocolErrors:
    """Verify JSON-RPC level failures."""

    def test_unknown_method(self) -> None:
        result = call(build(), "resources/list")
        assert result["error"]["code"] == METHOD_NOT_FOUND

    def test_malformed_json(self) -> None:
        result = build().handle_line("{not json")
        assert result["error"]["code"] == PARSE_ERROR

    def test_non_object_message(self) -> None:
        result = build().handle_line("[1, 2, 3]")
        assert result["error"]["code"] < 0

    def test_ping_is_answered(self) -> None:
        assert call(build(), "ping")["result"] == {}

    def test_notification_gets_no_reply(self) -> None:
        """A message with no id must not produce output."""
        assert build().handle({"jsonrpc": "2.0", "method": "notifications/x"}) is None


class TestTransport:
    """Verify the stdio loop."""

    def test_round_trip_over_streams(self) -> None:
        lines = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
        out = io.StringIO()
        build().serve(io.StringIO("\n".join(lines) + "\n"), out)

        replies = [json.loads(line) for line in out.getvalue().splitlines()]
        # The notification produced nothing, so two replies for three messages.
        assert [r["id"] for r in replies] == [1, 2]

    def test_blank_lines_are_skipped(self) -> None:
        out = io.StringIO()
        message = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        build().serve(io.StringIO(f"\n\n{message}\n\n"), out)
        assert len(out.getvalue().splitlines()) == 1

    def test_each_reply_is_one_line(self) -> None:
        """The framing is one JSON object per line."""
        out = io.StringIO()
        messages = "\n".join(
            json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping"}) for i in range(3)
        )
        build().serve(io.StringIO(messages + "\n"), out)
        assert len(out.getvalue().strip().splitlines()) == 3


class TestInternalFailure:
    """Verify that a crash inside dispatch becomes a JSON-RPC error."""

    def test_dispatch_failure_is_reported(self, monkeypatch) -> None:
        from ish.interfaces.mcp import protocol

        server = build()

        def boom(method, params):
            raise RuntimeError("the index is corrupt")

        monkeypatch.setattr(server, "_dispatch", boom)
        result = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})

        assert result["error"]["code"] == protocol.INTERNAL_ERROR
        assert "the index is corrupt" in result["error"]["message"]
