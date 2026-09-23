import * as assert from "node:assert/strict";
import { test } from "node:test";

import { iconFor, parseResult, parseResults } from "../src/results";

test("a plain result line is read apart", () => {
  assert.deepEqual(parseResult("[0.71] src/app.py:14-31  function  parse_config"), {
    score: 0.71,
    path: "src/app.py",
    startLine: 14,
    endLine: 31,
    kind: "function",
    symbol: "parse_config",
  });
});

test("a path with a colon is kept whole", () => {
  const result = parseResult("[0.50] C:/work/a.py:1-2  class  A");
  assert.equal(result?.path, "C:/work/a.py");
  assert.equal(result?.startLine, 1);
});

test("an anonymous symbol is kept as printed", () => {
  const result = parseResult("[0.30] docs/x.md:1-3  document  <anonymous>");
  assert.equal(result?.symbol, "<anonymous>");
});

test("prose is not a result", () => {
  assert.equal(parseResult("No results for 'x' under /tree."), undefined);
  assert.equal(parseResult(""), undefined);
});

test("a listing keeps only the results", () => {
  const results = parseResults([
    "[0.71] src/app.py:14-31  function  parse_config",
    "some warning",
    "[0.52] docs/guide.md:1-9  section  Guide",
  ]);
  assert.deepEqual(
    results.map((r) => r.symbol),
    ["parse_config", "Guide"],
  );
});

test("a kind maps to an icon", () => {
  assert.equal(iconFor("class"), "symbol-class");
  assert.equal(iconFor("method"), "symbol-method");
  assert.equal(iconFor("section"), "book");
  assert.equal(iconFor("mapping"), "symbol-misc");
});
