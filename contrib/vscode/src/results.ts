// Read a result line the server prints in the plain shape.
//
// `[0.71] src/app.py:14-31  function  parse_config` carries the score,
// the path, the line range, the kind, and the symbol. The path is
// relative to the tree the server was started in when it can be, and
// absolute when the file lies outside it.

export interface Result {
  score: number;
  /** The path as printed, relative to the server's tree or absolute. */
  path: string;
  startLine: number;
  endLine: number;
  kind: string;
  symbol: string;
}

// The path is greedy and may hold colons, so the range that follows
// it is anchored by the two spaces the format puts after it.
const LINE = /^\[(\d+(?:\.\d+)?)\] (.+):(\d+)-(\d+) {2}(\S+) {2}(.*)$/;

/** Read one line, or return undefined for a line that is not a result. */
export function parseResult(line: string): Result | undefined {
  const match = LINE.exec(line);
  if (match === null) {
    return undefined;
  }
  return {
    score: Number(match[1]),
    path: match[2],
    startLine: Number(match[3]),
    endLine: Number(match[4]),
    kind: match[5],
    symbol: match[6],
  };
}

/** Read every result line and drop the rest. */
export function parseResults(lines: readonly string[]): Result[] {
  const results: Result[] = [];
  for (const line of lines) {
    const result = parseResult(line);
    if (result !== undefined) {
      results.push(result);
    }
  }
  return results;
}

/** The codicon that stands for a chunk kind in the list. */
export function iconFor(kind: string): string {
  switch (kind) {
    case "class":
      return "symbol-class";
    case "function":
    case "async_function":
    case "method":
    case "async_method":
      return "symbol-method";
    case "section":
    case "document":
      return "book";
    default:
      return "symbol-misc";
  }
}
