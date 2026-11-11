// Test the pieces of the ask command that need no editor.

import * as assert from "node:assert/strict";
import { test } from "node:test";

import { answerName, answerOf, askArgs, run } from "../src/ask";

/** Run a line of JavaScript as the command, in place of ish. */
function script(body: string, signal?: AbortSignal) {
  return run(process.execPath, ["-e", body], __dirname, signal);
}

test("the question follows the flag and the separator", () => {
  assert.deepEqual(askArgs("-v is what", "/tree"), ["ask", "--", "-v is what", "/tree"]);
});

test("an answer is the text on stdout", () => {
  const outcome = { code: 0, stdout: "# q\n\nAn answer.\n", stderr: "usage\n", cancelled: false };
  assert.deepEqual(answerOf(outcome), { markdown: "# q\n\nAn answer." });
});

test("a question the documents do not cover still has an answer", () => {
  const outcome = { code: 2, stdout: "# q\n\n## Closest sources\n", stderr: "", cancelled: false };
  assert.deepEqual(answerOf(outcome), { markdown: "# q\n\n## Closest sources" });
});

test("no text gives the last line of stderr as the reason", () => {
  const outcome = {
    code: 1,
    stdout: "",
    stderr: "warning: one\nish: no language model is configured\n\n",
    cancelled: false,
  };
  assert.deepEqual(answerOf(outcome), { reason: "ish: no language model is configured" });
});

test("no text and no stderr gives the exit code as the reason", () => {
  assert.deepEqual(answerOf({ code: 3, stdout: " \n", stderr: "", cancelled: false }), {
    reason: "exit code 3",
  });
  assert.deepEqual(answerOf({ code: null, stdout: "", stderr: "", cancelled: false }), {
    reason: "exit code unknown",
  });
});

test("an answer is named after its question", () => {
  assert.equal(answerName("How is the watchdog configured?"), "how-is-the-watchdog-configured.md");
  assert.equal(answerName("lang:cpp retry/backoff"), "lang-cpp-retry-backoff.md");
  assert.equal(answerName("hur länge är perioden"), "hur-länge-är-perioden.md");
});

test("a long question is cut at a word", () => {
  const name = answerName("which registers control the watchdog timer and what are their reset values");
  assert.equal(name, "which-registers-control-the-watchdog-timer-and.md");
});

test("a question with no letter still has a name", () => {
  assert.equal(answerName("???"), "answer.md");
});

test("a run that exits with 0 keeps both streams", async () => {
  const outcome = await script('process.stdout.write("answer"); process.stderr.write("usage")');
  assert.deepEqual(outcome, { code: 0, stdout: "answer", stderr: "usage", cancelled: false });
});

test("a run that exits with 2 keeps its code and its text", async () => {
  const outcome = await script('process.stdout.write("closest"); process.exit(2)');
  assert.deepEqual(outcome, { code: 2, stdout: "closest", stderr: "", cancelled: false });
});

test("a run receives its arguments and its directory", async () => {
  const outcome = await run(
    process.execPath,
    // node stops reading its own options at the first `--`.
    ["-e", "console.log(JSON.stringify([process.argv.slice(1), process.cwd()]))", "--", ...askArgs("-q", "/tree")],
    __dirname,
  );
  assert.deepEqual(JSON.parse(outcome.stdout), [["ask", "--", "-q", "/tree"], __dirname]);
});

test("a command that does not exist says so and does not reject", async () => {
  const outcome = await run("ish-that-is-not-installed", [], __dirname);
  assert.equal(outcome.code, null);
  assert.equal(outcome.cancelled, false);
  assert.match(outcome.stderr, /ish-that-is-not-installed/);
  assert.deepEqual(answerOf(outcome), { reason: outcome.stderr.trim() });
});

test("an aborted run stops and reports that it was stopped", async () => {
  const stop = new AbortController();
  const waiting = script("setInterval(() => {}, 1000)", stop.signal);
  stop.abort();
  const outcome = await waiting;
  assert.equal(outcome.cancelled, true);
  assert.equal(outcome.code, null);
});
