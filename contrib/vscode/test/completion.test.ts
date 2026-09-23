import * as assert from "node:assert/strict";
import { test } from "node:test";

import { isFilterWord, lastWord, takeCandidate } from "../src/completion";

test("the last word follows the last space", () => {
  assert.equal(lastWord("state machine ty"), "ty");
  assert.equal(lastWord("ty"), "ty");
  assert.equal(lastWord("state machine "), "");
});

test("a prefix of a key is a filter word", () => {
  assert.equal(isFilterWord("t"), true);
  assert.equal(isFilterWord("state machine un"), true);
});

test("a key with a value under way is a filter word", () => {
  assert.equal(isFilterWord("lang:cp"), true);
  assert.equal(isFilterWord("type:"), true);
});

test("an ordinary word is not", () => {
  assert.equal(isFilterWord("exposure"), false);
  assert.equal(isFilterWord("state machine "), false);
  assert.equal(isFilterWord(""), false);
});

test("a value candidate is written after its key with a space", () => {
  assert.equal(takeCandidate("state lang:cp", "cpp"), "state lang:cpp ");
  assert.equal(takeCandidate("under:/s", "/src/"), "under:/src/ ");
});

test("a key candidate replaces the word and takes no space", () => {
  assert.equal(takeCandidate("state ty", "type:"), "state type:");
  assert.equal(takeCandidate("ty", "type:"), "type:");
});
