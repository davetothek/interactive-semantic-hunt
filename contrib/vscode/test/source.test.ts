import { strict as assert } from "node:assert";
import { test } from "node:test";

import { BuildAnswers, type Choice, choose, type Mode } from "../src/source";

/** A probe whose answers the test sets, and which counts the questions. */
function probe(hasIndex: boolean, serverUp: boolean, agree: boolean) {
  const asked = { build: 0 };
  return {
    asked,
    probe: {
      hasIndex: async () => hasIndex,
      serverUp: async () => serverUp,
      agreeToBuild: async () => {
        asked.build++;
        return agree;
      },
    },
  };
}

async function chosen(mode: Mode, hasIndex: boolean, serverUp: boolean, agree = false) {
  const made = probe(hasIndex, serverUp, agree);
  const choice: Choice | undefined = await choose(mode, made.probe);
  return { choice, asked: made.asked.build };
}

test("auto reads both sides when both are there, and asks nothing", async () => {
  assert.deepEqual(await chosen("auto", true, true), {
    choice: { local: true, remote: true },
    asked: 0,
  });
});

test("auto reads the server alone when the tree has no index", async () => {
  assert.deepEqual(await chosen("auto", false, true), {
    choice: { local: false, remote: true },
    asked: 0,
  });
});

test("auto reads this tree alone when no server is set", async () => {
  assert.deepEqual(await chosen("auto", true, false), {
    choice: { local: true, remote: false },
    asked: 0,
  });
});

test("auto with neither side asks before it builds", async () => {
  assert.deepEqual(await chosen("auto", false, false, true), {
    choice: { local: true, remote: false },
    asked: 1,
  });
  assert.deepEqual(await chosen("auto", false, false, false), {
    choice: undefined,
    asked: 1,
  });
});

test("local asks before it builds, and never reads the server", async () => {
  assert.deepEqual(await chosen("local", false, true, false), { choice: undefined, asked: 1 });
  assert.deepEqual(await chosen("local", true, true), {
    choice: { local: true, remote: false },
    asked: 0,
  });
});

test("remote reads the server alone, or nothing when none answers", async () => {
  assert.deepEqual(await chosen("remote", true, true), {
    choice: { local: false, remote: true },
    asked: 0,
  });
  assert.deepEqual(await chosen("remote", true, false), { choice: undefined, asked: 0 });
});

test("the question comes once for each tree, and the answer stands", async () => {
  const asked: string[] = [];
  const answers = new BuildAnswers(async (root) => {
    asked.push(root);
    return root === "/yes";
  });
  assert.equal(await answers.agree("/no"), false);
  assert.equal(await answers.agree("/no"), false);
  assert.equal(await answers.agree("/yes"), true);
  assert.equal(await answers.agree("/yes"), true);
  assert.deepEqual(asked, ["/no", "/yes"]);
  answers.clear();
  await answers.agree("/no");
  assert.deepEqual(asked, ["/no", "/yes", "/no"]);
});
