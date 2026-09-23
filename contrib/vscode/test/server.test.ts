// Test the client against a fake server that speaks the protocol.

import * as assert from "node:assert/strict";
import { join } from "node:path";
import { test } from "node:test";

import { IshServer, parseStatus } from "../src/server";

const FAKE = join(__dirname, "fake_server.js");

function fake(extra: Partial<ConstructorParameters<typeof IshServer>[0]> = {}) {
  return new IshServer({
    command: process.execPath,
    args: [FAKE],
    cwd: __dirname,
    timeoutMs: 5_000,
    ...extra,
  });
}

test("a tool call returns its text", async () => {
  const server = fake();
  try {
    const text = await server.callTool("echo", { query: "hello" });
    assert.deepEqual(JSON.parse(text), { query: "hello" });
    assert.equal(server.running, true);
  } finally {
    server.stop();
  }
});

test("the process is spawned once and reused", async () => {
  const server = fake();
  try {
    await server.callTool("echo", { n: 1 });
    const first = (server as unknown as { child: { pid: number } }).child.pid;
    await server.callTool("echo", { n: 2 });
    const second = (server as unknown as { child: { pid: number } }).child.pid;
    assert.equal(first, second);
  } finally {
    server.stop();
  }
});

test("a tool failure rejects with the reason", async () => {
  const server = fake();
  try {
    await assert.rejects(server.callTool("fail", {}), /the tool broke/);
  } finally {
    server.stop();
  }
});

test("replies are matched by id, whatever order they arrive in", async () => {
  const server = fake();
  try {
    const slow = server.callTool("slow", { ms: 150 });
    const quick = server.callTool("echo", { quick: true });
    assert.deepEqual(JSON.parse(await quick), { quick: true });
    assert.equal(await slow, "late");
  } finally {
    server.stop();
  }
});

test("a reply split across two writes is joined", async () => {
  const server = fake();
  try {
    assert.equal(await server.callTool("split", {}), "joined");
  } finally {
    server.stop();
  }
});

/** Wait until *ready* holds, or give up after a second. */
async function until(ready: () => boolean): Promise<void> {
  for (let waited = 0; waited < 1_000 && !ready(); waited += 10) {
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

test("stderr goes to the log and stdout noise is ignored", async () => {
  const logged: string[] = [];
  const server = fake({ log: (line) => logged.push(line) });
  try {
    assert.equal(await server.callTool("noise", {}), "after the noise");
    // stderr is its own pipe, so it may land after the reply did.
    await until(() => logged.length > 0);
    assert.deepEqual(logged, ["a warning on stderr"]);
  } finally {
    server.stop();
  }
});

test("a server that exits settles what was pending and can restart", async () => {
  const reasons: string[] = [];
  const server = fake({ onStopped: (reason) => reasons.push(reason) });
  try {
    await assert.rejects(server.callTool("crash", {}), /stopped with code 3/);
    assert.equal(server.running, false);
    assert.deepEqual(reasons, ["the server stopped with code 3"]);
    // The next question starts a new process.
    assert.deepEqual(JSON.parse(await server.callTool("echo", { again: 1 })), {
      again: 1,
    });
    assert.equal(server.running, true);
  } finally {
    server.stop();
  }
});

test("a command that cannot start rejects rather than hangs", async () => {
  const reasons: string[] = [];
  const server = new IshServer({
    command: join(__dirname, "no-such-command"),
    cwd: __dirname,
    timeoutMs: 5_000,
    onStopped: (reason) => reasons.push(reason),
  });
  await assert.rejects(server.callTool("echo", {}), /cannot start/);
  assert.equal(server.running, false);
  assert.equal(reasons.length, 1);
});

test("stop rejects what is in flight", async () => {
  const server = fake();
  const slow = server.callTool("slow", { ms: 2_000 });
  server.stop();
  await assert.rejects(slow, /was stopped/);
  assert.equal(server.running, false);
});

test("search returns only result lines", async () => {
  const server = fake();
  try {
    const lines = await server.search("parse config", "/tree", 40);
    assert.equal(lines.length, 2);
    assert.match(lines[0], /^\[0\.71\]/);
  } finally {
    server.stop();
  }
});

test("status reads what the refresh is doing and the chunk count", async () => {
  const server = fake();
  try {
    assert.deepEqual(await server.status("/tree"), {
      refreshing: "Reading 3 of 9 files",
      chunks: 104,
    });
  } finally {
    server.stop();
  }
});

test("complete reads the text and the candidates", async () => {
  const server = fake();
  try {
    assert.deepEqual(await server.complete("type:c", "/tree"), {
      text: "type:co",
      candidates: ["code", "config"],
    });
  } finally {
    server.stop();
  }
});

test("an idle status has no refreshing field", () => {
  const state = parseStatus("Index for x\n  refreshing: no\n  chunks   : 7\n");
  assert.deepEqual(state, { chunks: 7 });
});

test("a status with nothing readable is empty", () => {
  assert.deepEqual(parseStatus("garbage"), {});
});
