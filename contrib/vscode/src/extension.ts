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

import { isFilterWord, takeCandidate } from "./completion";
import { DONE, render as renderProgress } from "./progress";
import { iconFor, parseResults, type Result } from "./results";
import { IshServer } from "./server";

/** How often to ask what the index is doing, while it is doing it. */
const WATCH_MS = 700;
/** Give up watching after this long. A refresh of hours reports on
 * its own the next time a picker opens. */
const WATCH_LIMIT_MS = 10 * 60 * 1000;
/** How long to leave the finished mark up before clearing it. */
const DONE_MS = 1500;
/** How long after a save to wait for the next one before refreshing.
 * A format-on-save writes a file twice within a moment. */
const SAVE_DEBOUNCE_MS = 1000;

/** One list entry. A result opens a file. A candidate finishes a word. */
interface Item extends vscode.QuickPickItem {
  result?: Result;
  candidate?: string;
}

/** The picker in view, for the Tab command to reach. */
interface Picker {
  pick: vscode.QuickPick<Item>;
  complete: () => Promise<void>;
}
let active: Picker | undefined;

/** Tell the keybinding whether Tab means completion right now. */
function setPickerOpen(open: boolean): void {
  void vscode.commands.executeCommand("setContext", "ish.pickerOpen", open);
}

interface Settings {
  command: string;
  args: string[];
  limit: number;
  debounceMs: number;
  preview: boolean;
  refreshOnSave: boolean;
}

function settings(): Settings {
  const read = vscode.workspace.getConfiguration("ish");
  return {
    command: read.get<string>("command", "ish-mcp"),
    args: read.get<string[]>("args", []),
    limit: read.get<number>("limit", 40),
    debounceMs: read.get<number>("debounceMs", 120),
    preview: read.get<boolean>("preview", true),
    refreshOnSave: read.get<boolean>("refreshOnSave", true),
  };
}

/** One server per workspace folder, started on first use. */
class Servers implements vscode.Disposable {
  private readonly byRoot = new Map<string, IshServer>();

  constructor(private readonly output: vscode.OutputChannel) {}

  /** Whether a server has been started for *root*. */
  has(root: string): boolean {
    return this.byRoot.has(root);
  }

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
 * Follow a refresh until it finishes, drawing what it is doing.
 *
 * The index is brought up to date when the picker opens, not on every
 * keystroke, and the results improve while it runs. Say so in the
 * status bar: a picker that quietly answers from yesterday's index is
 * worse than a slow one. One item serves every tree, and a new watch
 * replaces the one before it.
 */
class IndexWatch implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private generation = 0;
  private timer: NodeJS.Timeout | undefined;

  constructor() {
    this.item = vscode.window.createStatusBarItem(
      "ish.index",
      vscode.StatusBarAlignment.Left,
      10,
    );
    this.item.name = "ish index";
  }

  /** Ask *server* to refresh *root*, and watch it until it is done. */
  start(server: IshServer, root: string): void {
    const mine = ++this.generation;
    clearTimeout(this.timer);
    let waited = 0;
    let told = false;

    const show = (text: string): void => {
      this.item.text = renderProgress(text);
      this.item.tooltip = text === DONE ? "ish: the index is up to date" : `ish: ${text}`;
      this.item.show();
    };
    const finish = (sayDone: boolean): void => {
      if (!sayDone) {
        this.item.hide();
        return;
      }
      // Leave a mark for a moment, so a refresh that finished can be
      // told from one that never ran.
      show(DONE);
      this.timer = setTimeout(() => {
        if (mine === this.generation) {
          this.item.hide();
        }
      }, DONE_MS);
    };
    const tick = async (): Promise<void> => {
      if (mine !== this.generation) {
        return;
      }
      waited += WATCH_MS;
      if (waited > WATCH_LIMIT_MS) {
        finish(false);
        return;
      }
      let state;
      try {
        state = await server.status(root);
      } catch {
        finish(false); // the server stopped answering
        return;
      }
      if (mine !== this.generation) {
        return;
      }
      if (state.refreshing !== undefined) {
        told = true;
        show(state.refreshing);
        this.timer = setTimeout(() => void tick(), WATCH_MS);
      } else {
        finish(told);
      }
    };

    void server.refresh(root).then(
      () => void tick(),
      () => finish(false), // the picker reports a server that is down
    );
  }

  dispose(): void {
    this.generation++;
    clearTimeout(this.timer);
    this.item.dispose();
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
 * The choices a filter word could still become, as entries to take.
 *
 * A QuickPick has no hook for a keystroke inside its input, so the
 * candidates sit at the head of the list where Enter takes one, and
 * Tab, bound while the picker is open, grows the word the way a shell
 * does. Both finish the same word through the same server call.
 */
function toCandidates(candidates: readonly string[], value: string): Item[] {
  return candidates.map((candidate) => ({
    label: `$(filter) ${takeCandidate(value, candidate).trimEnd()}`,
    description: "finish the filter word",
    alwaysShow: true,
    candidate,
  }));
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
function search(servers: Servers, watch: IndexWatch, root: string, seed = ""): void {
  const wanted = settings();
  const server = servers.for(root);
  // Look for changes now, once, rather than on every keystroke. The
  // search runs against whatever is already stored and improves as the
  // refresh lands.
  watch.start(server, root);
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

  const results = async (query: string): Promise<Item[]> => {
    try {
      const lines = await server.search(query, root, wanted.limit);
      const items = parseResults(lines).map(toItem);
      return items.length > 0 ? items : [toNotice(`No results for ${query}`)];
    } catch (error) {
      return [toNotice(error instanceof Error ? error.message : String(error))];
    }
  };

  // Offer the choices while a filter word is under way. An ordinary
  // word costs no extra call, because the keys are known here.
  const candidates = async (value: string): Promise<Item[]> => {
    if (!isFilterWord(value)) {
      return [];
    }
    try {
      const answer = await server.complete(value, root);
      return toCandidates(answer.candidates, value);
    } catch {
      return [];
    }
  };

  const run = async (value: string): Promise<void> => {
    const mine = ++generation;
    const query = value.trim();
    if (query === "") {
      pick.items = [];
      return;
    }
    pick.busy = true;
    const [offered, found] = await Promise.all([candidates(value), results(query)]);
    if (mine !== generation) {
      return;
    }
    pick.busy = false;
    pick.items = [...offered, ...found];
  };

  // Tab. Grow the word as far as the choices agree, and search again
  // from the grown query. The candidates then appear at the head.
  const complete = async (): Promise<void> => {
    const value = pick.value;
    let answer;
    try {
      answer = await server.complete(value, root);
    } catch {
      return;
    }
    if (pick.value !== value) {
      return; // typing went on, so the answer is for a word gone by
    }
    if (answer.text !== value) {
      pick.value = answer.text;
    }
    clearTimeout(timer);
    await run(answer.text);
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
    if (chosen?.candidate !== undefined) {
      // Take the word and stay open. The query is not done yet.
      pick.value = takeCandidate(pick.value, chosen.candidate);
      clearTimeout(timer);
      void run(pick.value);
      return;
    }
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
    if (active?.pick === pick) {
      active = undefined;
      setPickerOpen(false);
    }
    pick.dispose();
    if (!accepted) {
      void restore(before, shown);
    }
  });

  active = { pick, complete };
  setPickerOpen(true);
  pick.show();
  if (seed !== "") {
    pick.value = seed;
    void run(seed);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("ish");
  const servers = new Servers(output);
  const watch = new IndexWatch();
  context.subscriptions.push(output, servers, watch);
  let saveTimer: NodeJS.Timeout | undefined;
  context.subscriptions.push({ dispose: () => clearTimeout(saveTimer) });

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
      withRoot((root) => search(servers, watch, root)),
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
        search(servers, watch, root, seed);
      }),
    ),
    vscode.commands.registerCommand(
      "ish.refresh",
      withRoot((root) => watch.start(servers.for(root), root)),
    ),
    vscode.commands.registerCommand("ish.complete", () => {
      void active?.complete();
    }),
    // The editor is the one place that knows a file was saved, so tell
    // the server then, rather than have it find out on its next poll,
    // up to `refresh_seconds` later. Only a tree somebody has searched
    // has a server, so a save elsewhere starts nothing.
    vscode.workspace.onDidSaveTextDocument((document) => {
      if (!settings().refreshOnSave || document.uri.scheme !== "file") {
        return;
      }
      const root = vscode.workspace.getWorkspaceFolder(document.uri)?.uri.fsPath;
      if (root === undefined || !servers.has(root)) {
        return;
      }
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => watch.start(servers.for(root), root), SAVE_DEBOUNCE_MS);
    }),
    vscode.commands.registerCommand("ish.restart", () => {
      servers.dispose();
      output.appendLine("Stopped every server. The next search starts one.");
    }),
  );
}

export function deactivate(): void {
  // Every server is a subscription, so the context disposes it.
}
