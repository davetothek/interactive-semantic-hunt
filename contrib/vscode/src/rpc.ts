// JSON-RPC 2.0 framing, one message per line.
//
// The server reads a request per line and answers a reply per line.
// This module knows nothing of processes: it turns a stream of text
// into lines, and matches a reply back to the request that asked.

export interface RpcError {
  code: number;
  message: string;
}

/** A settled reply, as the server sends it. */
export interface Reply {
  id: number;
  result?: unknown;
  error?: RpcError;
}

/** Turn a stream of chunks into complete lines. */
export class LineBuffer {
  private rest = "";

  /** Take one chunk and return every line it completes. */
  push(chunk: string): string[] {
    const parts = (this.rest + chunk).split("\n");
    // The last part is whatever follows the final newline. It is a
    // partial line, or empty, and either way it waits for more.
    this.rest = parts.pop() ?? "";
    return parts.filter((line) => line.length > 0);
  }
}

/** Encode one request. The caller keeps the id to match the reply. */
export function request(
  id: number,
  method: string,
  params?: unknown,
): string {
  const message: Record<string, unknown> = { jsonrpc: "2.0", id, method };
  if (params !== undefined) {
    message.params = params;
  }
  return JSON.stringify(message) + "\n";
}

/** Encode one notification, which takes no reply. */
export function notification(method: string, params?: unknown): string {
  const message: Record<string, unknown> = { jsonrpc: "2.0", method };
  if (params !== undefined) {
    message.params = params;
  }
  return JSON.stringify(message) + "\n";
}

interface Waiter {
  resolve: (value: unknown) => void;
  reject: (reason: Error) => void;
  timer?: NodeJS.Timeout;
}

/** Hold the requests in flight and settle each when its reply arrives. */
export class Pending {
  private nextId = 0;
  private readonly waiting = new Map<number, Waiter>();

  /** How many requests wait for a reply. */
  get size(): number {
    return this.waiting.size;
  }

  /**
   * Open a request. Return its id and the promise its reply settles.
   *
   * A request that waits past *timeoutMs* is rejected, so a server
   * that hangs cannot hold a picker for ever. Zero means no limit.
   */
  open(timeoutMs = 0): { id: number; promise: Promise<unknown> } {
    const id = ++this.nextId;
    const promise = new Promise<unknown>((resolve, reject) => {
      const waiter: Waiter = { resolve, reject };
      if (timeoutMs > 0) {
        waiter.timer = setTimeout(() => {
          this.waiting.delete(id);
          reject(new Error(`no answer in ${timeoutMs} ms`));
        }, timeoutMs);
      }
      this.waiting.set(id, waiter);
    });
    return { id, promise };
  }

  /**
   * Settle the request one line of server output answers.
   *
   * Return false for a line that answers nothing: a notification, a
   * reply to a request nobody waits for, or text that is not JSON.
   * A server must not be able to break the client by writing badly.
   */
  settle(line: string): boolean {
    let message: unknown;
    try {
      message = JSON.parse(line);
    } catch {
      return false;
    }
    if (typeof message !== "object" || message === null) {
      return false;
    }
    const reply = message as Partial<Reply>;
    if (typeof reply.id !== "number") {
      return false;
    }
    const waiter = this.waiting.get(reply.id);
    if (waiter === undefined) {
      return false;
    }
    this.waiting.delete(reply.id);
    clearTimeout(waiter.timer);
    if (reply.error !== undefined) {
      waiter.reject(new Error(reply.error.message));
    } else {
      waiter.resolve(reply.result);
    }
    return true;
  }

  /** Reject every request in flight. Nothing will answer them. */
  failAll(reason: string): void {
    for (const waiter of this.waiting.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error(reason));
    }
    this.waiting.clear();
  }
}
