// Ask a question through the command line, and read what came back.
//
// `ish ask` writes the answer as Markdown on stdout, with each source
// as a link. Running the command line keeps one set of settings: the
// language model and the filters are the ones a shell uses, so an
// answer here is the answer there. It answers from the tree on this
// machine, as the resident server does.

import { execFile } from "node:child_process";

/** The most output one answer may hold, far above any real one. */
const MAX_OUTPUT_BYTES = 16 * 1024 * 1024;
/** How long the name of an answer may be before its extension. */
const MAX_NAME_CHARS = 48;

/** What one run of the command line left behind. */
export interface Outcome {
  /** The exit code, or null when the command did not start or was stopped. */
  code: number | null;
  stdout: string;
  stderr: string;
  /** Whether the person stopped the run. */
  cancelled: boolean;
}

/** An answer to show, or the reason there is none. */
export type Answer = { markdown: string } | { reason: string };

/**
 * The arguments that ask *question* about the tree at *root*.
 *
 * The question follows `--`, so one that starts with a hyphen is not
 * read as a flag.
 */
export function askArgs(question: string, root: string): string[] {
  return ["ask", "--", question, root];
}

/**
 * Read an answer out of *outcome*.
 *
 * Judge by the text, not the exit code: a question the documents do not
 * cover exits with 2 and still prints the closest sources. With no
 * text, the reason is the last line ish wrote on stderr.
 */
export function answerOf(outcome: Outcome): Answer {
  const markdown = outcome.stdout.trim();
  if (markdown !== "") {
    return { markdown };
  }
  const said = outcome.stderr
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
  return { reason: said.at(-1) ?? `exit code ${outcome.code ?? "unknown"}` };
}

/**
 * A file name for the answer to *question*, for the tab that shows it.
 *
 * Keep the letters and digits of the question, joined with hyphens, cut
 * at a word. The `.md` ending makes the document Markdown.
 */
export function answerName(question: string): string {
  const words = question.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  let name = "";
  for (const word of words) {
    const longer = name === "" ? word : `${name}-${word}`;
    if (longer.length > MAX_NAME_CHARS) {
      break;
    }
    name = longer;
  }
  return `${name === "" ? "answer" : name}.md`;
}

/**
 * Run *command* with *args* in *cwd*, and resolve with what it left.
 *
 * Never reject: a command that does not start resolves with a null
 * code and the reason on stderr, so one caller reads every ending.
 * Aborting *signal* stops the command.
 */
export function run(
  command: string,
  args: readonly string[],
  cwd: string,
  signal?: AbortSignal,
): Promise<Outcome> {
  return new Promise((resolve) => {
    execFile(
      command,
      [...args],
      { cwd, signal, encoding: "utf8", maxBuffer: MAX_OUTPUT_BYTES },
      (error, stdout, stderr) => {
        if (error === null) {
          resolve({ code: 0, stdout, stderr, cancelled: false });
          return;
        }
        const cancelled = error.name === "AbortError";
        // An exit code is a number. A command that did not start has a
        // name such as ENOENT there, and says why in its message.
        const exited = typeof error.code === "number";
        resolve({
          code: exited ? (error.code as number) : null,
          stdout,
          stderr: exited || cancelled ? stderr : error.message,
          cancelled,
        });
      },
    );
  });
}
