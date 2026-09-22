// The VS Code client of ish.
//
// It is a client of the resident `ish-mcp` server, the same shape as
// `contrib/nvim/`. Nothing here searches: the extension asks the server
// and draws the answer, so a query costs a round trip and no process
// start.

import * as vscode from "vscode";

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("ish.search", () => {
      void vscode.window.showInformationMessage(
        "ish: the picker is not built yet.",
      );
    }),
  );
}

export function deactivate(): void {
  // Nothing is held between activations yet.
}
