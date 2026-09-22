import * as assert from "node:assert/strict";
import { test } from "node:test";

import { DONE, fraction, render } from "../src/progress";

test("nothing reads as nothing", () => {
  assert.equal(fraction(undefined), undefined);
  assert.equal(fraction(""), undefined);
  assert.equal(render(undefined), "");
  assert.equal(render(""), "");
});

test("one count is a share of the work", () => {
  assert.equal(fraction("Reading 30 of 120 files"), 0.25);
  assert.equal(fraction("Embedded 5 of 5 chunks"), 1);
});

test("a count past its total stops at one", () => {
  assert.equal(fraction("Embedded 7 of 5 chunks"), 1);
});

test("a zero total is no progress", () => {
  assert.equal(fraction("Reading 0 of 0 files"), 0);
});

test("a tree among several places the share inside it", () => {
  // The second of four trees, half read: one whole tree plus a half,
  // out of four.
  assert.equal(fraction("Refreshing 2 of 4: docs, Reading 60 of 120 files"), 0.375);
});

test("a tree named before it reports counts is at its start", () => {
  assert.equal(fraction("Refreshing 3 of 4: docs, Looking for source files"), 0.5);
});

test("a line with no count has no fraction", () => {
  assert.equal(fraction("Looking for source files"), undefined);
  assert.equal(render("Looking for source files"), "ish …");
});

test("the bar fills with the share", () => {
  assert.equal(render("Reading 30 of 120 files"), "ish ██░░░░░░ 25%");
  assert.equal(render("Embedded 5 of 5 chunks"), "ish ████████ 100%");
  assert.equal(render("Reading 0 of 120 files"), "ish ░░░░░░░░  0%");
});

test("a finished refresh is a mark", () => {
  assert.equal(render(DONE), "ish ✓");
});
