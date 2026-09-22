// Check the extension manifest against what the code provides.
//
// VS Code reads `package.json` and the code separately, so a command
// declared in one and missing from the other fails only when a person
// clicks it. Keep the two in step here.

import * as assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const root = join(__dirname, "..", "..");
const manifest = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const source = readFileSync(join(root, "src", "extension.ts"), "utf8");

test("the entry point the manifest names is built", () => {
  assert.ok(existsSync(join(root, manifest.main)), manifest.main);
});

test("every declared command is registered by the extension", () => {
  for (const command of manifest.contributes.commands) {
    assert.match(source, new RegExp(`"${command.command}"`), command.command);
  }
});

test("every command sits under the ish category", () => {
  for (const command of manifest.contributes.commands) {
    assert.equal(command.category, "ish", command.command);
  }
});

test("the extension declares no activation event of its own", () => {
  // A declared command activates the extension on its own since
  // VS Code 1.74, so an explicit list would only fall out of step.
  assert.deepEqual(manifest.activationEvents, []);
});

test("every keybinding names a declared command", () => {
  const declared = new Set(
    manifest.contributes.commands.map((c: { command: string }) => c.command),
  );
  for (const binding of manifest.contributes.keybindings ?? []) {
    assert.ok(declared.has(binding.command), binding.command);
  }
});

test("every setting the manifest declares is read by the extension", () => {
  const properties = manifest.contributes.configuration?.properties ?? {};
  for (const name of Object.keys(properties)) {
    const key = name.replace(/^ish\./, "");
    assert.match(source, new RegExp(`\\.get<[^>]+>\\("${key}"`), name);
  }
});
