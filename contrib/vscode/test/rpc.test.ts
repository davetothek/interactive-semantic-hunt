// Test the framing: lines out of chunks, and replies back to requests.

import * as assert from "node:assert/strict";
import { test } from "node:test";

import { LineBuffer, notification, Pending, request } from "../src/rpc";

test("a chunk that ends a line yields it", () => {
  const buffer = new LineBuffer();
  assert.deepEqual(buffer.push('{"a":1}\n'), ['{"a":1}']);
});

test("a partial line waits for the rest", () => {
  const buffer = new LineBuffer();
  assert.deepEqual(buffer.push('{"a"'), []);
  assert.deepEqual(buffer.push(':1}\n{"b":2}\n{"c'), ['{"a":1}', '{"b":2}']);
  assert.deepEqual(buffer.push('":3}\n'), ['{"c":3}']);
});

test("empty lines are dropped", () => {
  assert.deepEqual(new LineBuffer().push("\n\n"), []);
});

test("a request carries an id and a notification carries none", () => {
  const sent = JSON.parse(request(7, "ping"));
  assert.deepEqual(sent, { jsonrpc: "2.0", id: 7, method: "ping" });
  const told = JSON.parse(notification("notifications/initialized"));
  assert.deepEqual(told, { jsonrpc: "2.0", method: "notifications/initialized" });
  assert.equal("params" in told, false);
});

test("params travel when given", () => {
  const sent = JSON.parse(request(1, "tools/call", { name: "x" }));
  assert.deepEqual(sent.params, { name: "x" });
});

test("a reply settles the request with its id", async () => {
  const pending = new Pending();
  const first = pending.open();
  const second = pending.open();
  assert.equal(pending.size, 2);
  assert.equal(
    pending.settle(JSON.stringify({ jsonrpc: "2.0", id: second.id, result: 2 })),
    true,
  );
  assert.equal(await second.promise, 2);
  assert.equal(pending.size, 1);
  pending.settle(JSON.stringify({ jsonrpc: "2.0", id: first.id, result: 1 }));
  assert.equal(await first.promise, 1);
});

test("an error reply rejects with its message", async () => {
  const pending = new Pending();
  const { id, promise } = pending.open();
  pending.settle(
    JSON.stringify({ jsonrpc: "2.0", id, error: { code: -1, message: "bad" } }),
  );
  await assert.rejects(promise, /bad/);
});

test("a line that answers nothing is ignored", () => {
  const pending = new Pending();
  pending.open();
  assert.equal(pending.settle("not json"), false);
  assert.equal(pending.settle("42"), false);
  assert.equal(pending.settle('{"jsonrpc":"2.0","method":"note"}'), false);
  assert.equal(pending.settle('{"jsonrpc":"2.0","id":999,"result":1}'), false);
  assert.equal(pending.size, 1);
});

test("failAll rejects every request in flight", async () => {
  const pending = new Pending();
  const a = pending.open();
  const b = pending.open();
  pending.failAll("the server stopped");
  await assert.rejects(a.promise, /the server stopped/);
  await assert.rejects(b.promise, /the server stopped/);
  assert.equal(pending.size, 0);
});

test("a request past its time limit is rejected", async () => {
  const pending = new Pending();
  const { promise } = pending.open(10);
  await assert.rejects(promise, /no answer in 10 ms/);
  assert.equal(pending.size, 0);
});
