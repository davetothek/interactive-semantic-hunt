"""Test that a transport only moves messages, and the server only answers them."""

import io
import json

from ish.interfaces.mcp.protocol import Server, Tool
from ish.interfaces.mcp.transport import StdioTransport, Transport


class Echo:
    """Answer every line with what it said, and stay quiet for 'hush'."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def handle_line(self, line: str):
        self.lines.append(line)
        return None if line == "hush" else {"said": line}


class TestStdioTransport:
    def test_each_line_gets_one_reply_line(self) -> None:
        out = io.StringIO()
        handler = Echo()
        StdioTransport(io.StringIO("a\n\n  b  \nhush\n"), out).serve(handler)
        assert handler.lines == ["a", "b", "hush"]
        assert [json.loads(r) for r in out.getvalue().splitlines()] == [
            {"said": "a"},
            {"said": "b"},
        ]

    def test_it_is_a_transport(self) -> None:
        assert isinstance(StdioTransport(io.StringIO(), io.StringIO()), Transport)


class TestServeOn:
    def test_the_server_answers_whatever_transport_carries_it(self) -> None:
        class OneMessage:
            def __init__(self) -> None:
                self.replies: list = []

            def serve(self, server) -> None:
                message = {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "hello", "arguments": {}},
                }
                self.replies.append(server.handle_line(json.dumps(message)))

        tool = Tool("hello", "Say hello.", {"type": "object"}, lambda args: "hi")
        transport = OneMessage()
        Server("t", "0", [tool]).serve_on(transport)
        assert transport.replies == [
            {
                "jsonrpc": "2.0",
                "id": 7,
                "result": {
                    "content": [{"type": "text", "text": "hi"}],
                    "isError": False,
                },
            }
        ]
