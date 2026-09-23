// One resident ish-mcp process per workspace folder.
//
// Spawning ish for every keystroke pays interpreter and library startup
// each time, about half a second. A resident server pays it once and
// answers in about a tenth of that. Starting is idempotent: the server
// is spawned on first use and reused after, so nothing runs until
// something asks a question.

import { spawn, type ChildProcess } from "node:child_process";

import { LineBuffer, notification, Pending, request } from "./rpc";

/** The protocol revision this client asks for. The server echoes it. */
export const PROTOCOL_VERSION = "2025-06-18";

/** How long a call may wait before it is given up. A cold model can
 * take seconds to load, so this is generous. */
export const CALL_TIMEOUT_MS = 60_000;

export interface ServerOptions {
  /** The command that starts the server, `ish-mcp` by default. */
  command: string;
  args?: string[];
  /** The tree the server is started in. It reads its config there. */
  cwd: string;
  /** Where the server's stderr goes, one line at a time. */
  log?: (line: string) => void;
  /** Called when the process goes away, with the reason. */
  onStopped?: (reason: string) => void;
  timeoutMs?: number;
}

/** What `index_status` reports, as far as a status bar needs it. */
export interface IndexState {
  /** What the refresh is doing, or undefined when nothing runs. */
  refreshing?: string;
  chunks?: number;
}

/** What `complete_filter` answers. */
export interface Completion {
  text: string;
  candidates: string[];
}

interface ToolResult {
  content?: Array<{ type: string; text?: string }>;
  isError?: boolean;
}

export class IshServer {
  private child: ChildProcess | undefined;
  private readonly pending = new Pending();
  private readonly lines = new LineBuffer();

  constructor(private readonly options: ServerOptions) {}

  /** Whether a process is up. */
  get running(): boolean {
    return this.child !== undefined;
  }

  /**
   * Start the server unless it is already running.
   *
   * A command that cannot start reports through the `error` event,
   * which arrives after this returns. The call that asked then fails
   * with the reason, so nothing waits for an answer that never comes.
   */
  ensure(): void {
    if (this.child !== undefined) {
      return;
    }
    const child = spawn(this.options.command, this.options.args ?? [], {
      cwd: this.options.cwd,
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child = child;

    child.stdout?.setEncoding("utf8");
    child.stdout?.on("data", (chunk: string) => {
      for (const line of this.lines.push(chunk)) {
        this.pending.settle(line);
      }
    });
    child.stderr?.setEncoding("utf8");
    const errors = new LineBuffer();
    child.stderr?.on("data", (chunk: string) => {
      for (const line of errors.push(chunk)) {
        this.options.log?.(line);
      }
    });
    child.on("error", (error: Error) => {
      this.gone(`cannot start ${this.options.command}: ${error.message}`);
    });
    child.on("exit", (code) => {
      this.gone(
        code === null || code === 0
          ? "the server stopped"
          : `the server stopped with code ${code}`,
      );
    });

    // Say hello. The server answers every call whether or not the
    // handshake is done, so nothing waits on it.
    void this.call("initialize", {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: { name: "ish-vscode" },
    })
      .then(() => this.write(notification("notifications/initialized")))
      .catch(() => undefined);
  }

  /** Stop the server. Starting again is a call to `ensure()`. */
  stop(): void {
    const child = this.child;
    if (child === undefined) {
      return;
    }
    this.child = undefined;
    child.kill();
    this.pending.failAll("the server was stopped");
  }

  /** Send one request and return its result. */
  call(method: string, params?: unknown): Promise<unknown> {
    this.ensure();
    const { id, promise } = this.pending.open(
      this.options.timeoutMs ?? CALL_TIMEOUT_MS,
    );
    if (!this.write(request(id, method, params))) {
      // Nothing will answer, so let the caller stop waiting now.
      this.pending.failAll("the server has gone");
    }
    return promise;
  }

  /** Call one tool and return its text. A tool failure rejects. */
  async callTool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<string> {
    const result = (await this.call("tools/call", {
      name,
      arguments: args,
    })) as ToolResult;
    const text = result.content?.[0]?.text ?? "";
    if (result.isError) {
      throw new Error(text || `${name} failed`);
    }
    return text;
  }

  /** Ask for ranked results, one line each, in the plain shape. */
  async search(
    query: string,
    path: string,
    limit: number,
  ): Promise<string[]> {
    const text = await this.callTool("search_code", {
      query,
      path,
      limit,
      format: "plain",
    });
    // "No results for ..." is prose, not a result.
    return text.split("\n").filter((line) => line.startsWith("["));
  }

  /** Ask the server to bring the index up to date, and return at once. */
  async refresh(path: string): Promise<void> {
    await this.callTool("refresh_index", { path });
  }

  /** Report what the index holds, and whether a refresh is running. */
  async status(path: string): Promise<IndexState> {
    const text = await this.callTool("index_status", { path });
    return parseStatus(text);
  }

  /** Finish the filter word *query* ends in. */
  async complete(query: string, path: string): Promise<Completion> {
    const text = await this.callTool("complete_filter", { query, path });
    const parsed = JSON.parse(text) as Partial<Completion>;
    return {
      text: typeof parsed.text === "string" ? parsed.text : query,
      candidates: Array.isArray(parsed.candidates) ? parsed.candidates : [],
    };
  }

  private write(text: string): boolean {
    const stdin = this.child?.stdin;
    if (stdin === undefined || stdin === null || stdin.destroyed) {
      return false;
    }
    try {
      stdin.write(text);
      return true;
    } catch {
      return false;
    }
  }

  private gone(reason: string): void {
    if (this.child === undefined) {
      return;
    }
    this.child = undefined;
    this.pending.failAll(reason);
    this.options.onStopped?.(reason);
  }
}

/** Read the two lines a status bar needs out of `index_status`. */
export function parseStatus(text: string): IndexState {
  const state: IndexState = {};
  const doing = /refreshing\s*:\s*([^\n]+)/.exec(text)?.[1]?.trim();
  if (doing !== undefined && doing !== "no") {
    state.refreshing = doing;
  }
  const chunks = /chunks\s*:\s*(\d+)/.exec(text)?.[1];
  if (chunks !== undefined) {
    state.chunks = Number(chunks);
  }
  return state;
}
