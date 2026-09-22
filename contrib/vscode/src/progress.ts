// Render what a refresh is doing, for a status bar.
//
// The server reports a line such as `Refreshing 2 of 4: docs, Reading
// 30 of 120 files`. Read the counts out of it and draw a bar, so a
// refresh of minutes can be told from a hang.

/** How wide to draw the bar. Narrow enough to sit in a status bar. */
export const BAR_WIDTH = 8;

/** What a finished refresh publishes. It carries no counts. */
export const DONE = "done";

/**
 * Read how far along a refresh is, from what it says it is doing.
 *
 * A refresh walks several trees and reports twice: which tree it is on,
 * and how far into that tree it has read. Combine them, so the bar
 * moves smoothly rather than jumping once per tree. Return undefined
 * when the line carries no count to read.
 */
export function fraction(text: string | undefined): number | undefined {
  if (text === undefined || text === "") {
    return undefined;
  }
  const counts: Array<[number, number]> = [];
  for (const match of text.matchAll(/(\d+) of (\d+)/g)) {
    counts.push([Number(match[1]), Number(match[2])]);
  }
  if (counts.length === 0) {
    return undefined;
  }
  const share = ([done, total]: [number, number]): number =>
    total > 0 ? Math.min(done / total, 1) : 0;

  if (!/Refreshing \d+ of \d+/.test(text)) {
    return share(counts[0]);
  }
  const [tree, treeCount] = counts[0];
  const trees = Math.max(treeCount, 1);
  const within = counts[1] !== undefined ? share(counts[1]) : 0;
  return Math.min((tree - 1 + within) / trees, 1);
}

/**
 * Render the index progress for a status bar. Empty when idle.
 *
 * Show a bar when the work can be measured and a plain mark when it
 * cannot, rather than a bar that does not move.
 */
export function render(text: string | undefined): string {
  if (text === undefined || text === "") {
    return "";
  }
  if (text === DONE) {
    return "ish ✓";
  }
  const part = fraction(text);
  if (part === undefined) {
    return "ish …";
  }
  const filled = Math.round(part * BAR_WIDTH);
  const bar = "█".repeat(filled) + "░".repeat(BAR_WIDTH - filled);
  return `ish ${bar} ${String(Math.round(part * 100)).padStart(2)}%`;
}
