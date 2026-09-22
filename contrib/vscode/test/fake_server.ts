// A stand-in for ish-mcp, for the client tests.
//
// Speak enough of the protocol to exercise the client: answer
// `initialize`, answer `tools/call` by tool name, and misbehave on
// request. Run it as `node fake_server.js`.

import * as readline from "node:readline";

interface Message {
  id?: number;
  method?: string;
  params?: { name?: string; arguments?: Record<string, unknown> };
}

function reply(id: number, result: unknown): void {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}

function text(id: number, value: string, isError = false): void {
  reply(id, { content: [{ type: "text", text: value }], isError });
}

const lines = readline.createInterface({ input: process.stdin });
lines.on("line", (line) => {
  const message = JSON.parse(line) as Message;
  if (message.id === undefined) {
    return; // a notification takes no reply
  }
  const id = message.id;
  if (message.method === "initialize") {
    reply(id, { protocolVersion: "2025-06-18", serverInfo: { name: "fake" } });
    return;
  }
  if (message.method !== "tools/call") {
    process.stdout.write(
      JSON.stringify({
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: `Unknown method: ${message.method}` },
      }) + "\n",
    );
    return;
  }
  const name = message.params?.name;
  const args = message.params?.arguments ?? {};
  switch (name) {
    case "echo":
      text(id, JSON.stringify(args));
      return;
    case "fail":
      text(id, "the tool broke", true);
      return;
    case "slow":
      setTimeout(() => text(id, "late"), Number(args.ms ?? 200));
      return;
    case "split": {
      // Write one reply in two pieces, so the client has to buffer.
      const whole = JSON.stringify({
        jsonrpc: "2.0",
        id,
        result: { content: [{ type: "text", text: "joined" }], isError: false },
      });
      process.stdout.write(whole.slice(0, 10));
      setTimeout(() => process.stdout.write(whole.slice(10) + "\n"), 20);
      return;
    }
    case "noise":
      process.stderr.write("a warning on stderr\n");
      process.stdout.write("not json at all\n");
      text(id, "after the noise");
      return;
    case "crash":
      process.exit(3);
    // falls through only in type space; exit never returns
    case "search_code":
      text(
        id,
        [
          "[0.71] src/app.py:14-31  function  parse_config",
          "[0.52] docs/guide.md:1-9  section  Guide",
        ].join("\n"),
      );
      return;
    case "index_status":
      text(
        id,
        [
          "Index for /tree",
          "  refreshing: Reading 3 of 9 files",
          "  chunks   : 104",
        ].join("\n"),
      );
      return;
    case "complete_filter":
      text(id, JSON.stringify({ text: "type:co", candidates: ["code", "config"] }));
      return;
    default:
      text(id, `Unknown tool: ${name}`, true);
  }
});
