// The VS Code client of ish.
//
// It is a client of the resident `ish-mcp` server, the same shape as
// `contrib/nvim/`. Nothing here searches: the extension asks the server
// and draws the answer, so a query costs a round trip and no process
// start. Filters typed into the query, such as `lang:cpp type:doc
// under:/src/`, travel in the query text and are read by the server,
// exactly as they are from the command line, the picker, and Neovim.

import * as path from "node:path";

import * as vscode from "vscode";

import { iconFor, parseResults, type Result } from "./results";
import { IshServer } from "./server";

/** One list entry. A result opens a file. */
interface Item extends vscode.QuickPickItem {
  result?: Result;
}

interface Settings {
  command: string;
  args: string[];
  limit: number;
  debounceMs: number;
  preview: boolean;
}

function settings(): Settings {
  const read = vscode.workspace.getConfiguration("ish");
  return {
    command: read.get<string>("command", "ish-mcp"),
    args: read.get<string[]>("args", []),
    limit: read.get<number>("limit", 40),
    debounceMs: read.get<number>("debounceMs", 120),
    preview: read.get<boolean>("preview", true),
  };
}

/** One server per workspace folder, started on first use. */
class Servers implements vscode.Disposable {
  private readonly byRoot = new Map<string, IshServer>();

  constructor(private readonly output: vscode.OutputChannel) {}

  for(root: string): IshServer {
    let server = this.byRoot.get(root);
    if (server === undefined) {
      const wanted = settings();
      server = new IshServer({
        command: wanted.command,
        args: wanted.args,
        cwd: root,
        log: (line) => this.output.appendLine(`[${path.basename(root)}] ${line}`),
        onStopped: (reason) => this.output.appendLine(`[${path.basename(root)}] ${reason}`),
      });
      this.byRoot.set(root, server);
    }
    return server;
  }

  /** Stop every server. The next question starts a new one. */
  dispose(): void {
    for (const server of this.byRoot.values()) {
      server.stop();
    }
    this.byRoot.clear();
  }
}

/**
 * The tree to search: the workspace folder of the active file, else
 * the first folder open. A file outside every folder searches its own
 * directory, which is what a picker opened from it would expect.
 */
function rootFor(editor: vscode.TextEditor | undefined): string | undefined {
  const document = editor?.document;
  if (document !== undefined && document.uri.scheme === "file") {
    const folder = vscode.workspace.getWorkspaceFolder(document.uri);
    if (folder !== undefined) {
      return folder.uri.fsPath;
    }
    return path.dirname(document.uri.fsPath);
  }
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function locate(root: string, result: Result): { uri: vscode.Uri; range: vscode.Range } {
  const uri = vscode.Uri.file(path.resolve(root, result.path));
  // Select the whole chunk: from the head of its first line to the head
  // of the line after its last. VS Code clamps a line past the end.
  const range = new vscode.Range(result.startLine - 1, 0, result.endLine, 0);
  return { uri, range };
}

function toItem(result: Result): Item {
  return {
    label: `$(${iconFor(result.kind)}) ${result.score.toFixed(2)}  ${result.symbol}`,
    description: result.kind,
    detail: `${result.path}:${result.startLine}-${result.endLine}`,
    // The list is ranked by the server, and the words typed are a
    // question rather than a label to match, so every entry stays.
    alwaysShow: true,
    result,
  };
}

function toNotice(text: string): Item {
  return { label: `$(info) ${text}`, alwaysShow: true };
}

/**
 * Show the highlighted chunk without leaving the picker.
 *
 * The index stores where a chunk is, never what it says, so the file
 * is read fresh, as the TUI's preview pane reads it. A preview editor
 * is reused by the next one, and the focus stays in the query field.
 */
async function preview(root: string, result: Result): Promise<void> {
  const { uri, range } = locate(root, result);
  const editor = await vscode.window.showTextDocument(uri, {
    selection: range,
    preview: true,
    preserveFocus: true,
    viewColumn: vscode.ViewColumn.Active,
  });
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}

/** Where the person was before the picker opened, to go back to. */
interface Place {
  document: vscode.TextDocument;
  viewColumn: vscode.ViewColumn | undefined;
  selection: vscode.Selection;
}

function placeOf(editor: vscode.TextEditor | undefined): Place | undefined {
  if (editor === undefined) {
    return undefined;
  }
  return {
    document: editor.document,
    viewColumn: editor.viewColumn,
    selection: editor.selection,
  };
}

/**
 * Put the editor back the way the picker found it.
 *
 * A cancelled picker must not leave the last previewed file in view.
 * When nothing was open before, close the preview it opened instead.
 */
async function restore(place: Place | undefined, shown: boolean): Promise<void> {
  if (!shown) {
    return;
  }
  if (place === undefined) {
    await vscode.commands.executeCommand("workbench.action.closeActiveEditor");
    return;
  }
  await vscode.window.showTextDocument(place.document, {
    viewColumn: place.viewColumn,
    selection: place.selection,
    preserveFocus: false,
  });
}

/** Open the chosen chunk for editing, with its lines selected. */
async function open(root: string, result: Result): Promise<void> {
  const { uri, range } = locate(root, result);
  const editor = await vscode.window.showTextDocument(uri, {
    selection: range,
    preview: false,
  });
  editor.revealRange(range, vscode.TextEditorRevealType.InCenter);
}

/** Run the picker over *root*, starting from *seed* in the query field. */
function search(servers: Servers, root: string, seed = ""): void {
  const wanted = settings();
  const server = servers.for(root);
  const before = placeOf(vscode.window.activeTextEditor);
  const pick = vscode.window.createQuickPick<Item>();
  pick.title = `ish: ${path.basename(root)}`;
  pick.placeholder = "Search by meaning, or narrow with lang:cpp type:doc under:/src/";
  pick.matchOnDescription = false;
  pick.matchOnDetail = false;

  // Which search is the current one. A reply for an older query is
  // dropped, so typing fast never shows results for a word gone by.
  let generation = 0;
  let timer: NodeJS.Timeout | undefined;

  const run = async (value: string): Promise<void> => {
    const mine = ++generation;
    const query = value.trim();
    if (query === "") {
      pick.items = [];
      return;
    }
    pick.busy = true;
    let items: Item[];
    try {
      const lines = await server.search(query, root, wanted.limit);
      items = parseResults(lines).map(toItem);
      if (items.length === 0) {
        items = [toNotice(`No results for ${query}`)];
      }
    } catch (error) {
      items = [toNotice(error instanceof Error ? error.message : String(error))];
    }
    if (mine !== generation) {
      return;
    }
    pick.busy = false;
    pick.items = items;
  };

  pick.onDidChangeValue((value) => {
    // Wait for the typing to pause. Every keystroke would otherwise
    // embed a query nobody wants an answer to.
    clearTimeout(timer);
    timer = setTimeout(() => void run(value), wanted.debounceMs);
  });

  // Follow the highlight with a preview. Each move supersedes the one
  // before, so a stale preview never lands on top of a newer one.
  let previewed = 0;
  let shown = false;
  pick.onDidChangeActive((active) => {
    const result = active[0]?.result;
    if (!wanted.preview || result === undefined) {
      return;
    }
    const mine = ++previewed;
    void preview(root, result).then(
      () => {
        if (mine === previewed) {
          shown = true;
        }
      },
      () => undefined,
    );
  });

  let accepted = false;
  pick.onDidAccept(() => {
    const chosen = pick.selectedItems[0];
    if (chosen?.result === undefined) {
      return;
    }
    accepted = true;
    pick.hide();
    void open(root, chosen.result);
  });

  pick.onDidHide(() => {
    clearTimeout(timer);
    generation++;
    previewed++;
    pick.dispose();
    if (!accepted) {
      void restore(before, shown);
    }
  });

  pick.show();
  if (seed !== "") {
    pick.value = seed;
    void run(seed);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("ish");
  const servers = new Servers(output);
  context.subscriptions.push(output, servers);

  const withRoot = (body: (root: string, editor: vscode.TextEditor | undefined) => void) => {
    return () => {
      const editor = vscode.window.activeTextEditor;
      const root = rootFor(editor);
      if (root === undefined) {
        void vscode.window.showWarningMessage("ish: open a folder or a file to search.");
        return;
      }
      body(root, editor);
    };
  };

  context.subscriptions.push(
    vscode.commands.registerCommand(
      "ish.search",
      withRoot((root) => search(servers, root)),
    ),
    vscode.commands.registerCommand(
      "ish.searchHere",
      withRoot((root, editor) => {
        // Narrow to the directory of the file in view, written as the
        // `under:` word the other interfaces take, so the query shows
        // how it was narrowed and the word can be edited away.
        const file = editor?.document.uri.fsPath;
        const here = file === undefined ? "" : path.relative(root, path.dirname(file));
        const seed = here === "" ? "" : `under:/${here.split(path.sep).join("/")}/ `;
        search(servers, root, seed);
      }),
    ),
    vscode.commands.registerCommand("ish.restart", () => {
      servers.dispose();
      output.appendLine("Stopped every server. The next search starts one.");
    }),
  );
}

export function deactivate(): void {
  // Every server is a subscription, so the context disposes it.
}
