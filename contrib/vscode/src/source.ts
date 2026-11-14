// Choose where a search goes: the tree on this machine, a server, or both.
//
// With "auto", the default, search the local index when there is one,
// the server when one is set, and both when both are. With neither, or
// with "local" and no index, ask before an index is built: a large tree
// takes hours to embed. The questions go through *probe*, so this module
// needs no editor to test.

/** Where the settings say a search goes. */
export type Mode = "auto" | "local" | "remote";

/** The sides a search reads. */
export interface Choice {
  local: boolean;
  remote: boolean;
}

/** What the choice asks of the editor. */
export interface Probe {
  /** Whether a stored index reaches the tree. Asking starts none. */
  hasIndex(): Promise<boolean>;
  /** Whether a server is set and answers. */
  serverUp(): Promise<boolean>;
  /** Whether the person agrees to build an index of the tree now. */
  agreeToBuild(): Promise<boolean>;
}

/**
 * Ask once for each tree whether to build its index, and keep the
 * answer for the session. A question at each search would soon be
 * noise: after a yes the index is under way, and a no stands.
 */
export class BuildAnswers {
  private readonly answers = new Map<string, boolean>();

  constructor(private readonly ask: (root: string) => Promise<boolean>) {}

  /** The answer for *root*, asked the first time only. */
  async agree(root: string): Promise<boolean> {
    const known = this.answers.get(root);
    if (known !== undefined) {
      return known;
    }
    const answer = await this.ask(root);
    this.answers.set(root, answer);
    return answer;
  }

  /** Forget every answer, as a restart of the servers does. */
  clear(): void {
    this.answers.clear();
  }
}

/** Return the sides to search, or undefined to search nothing. */
export async function choose(mode: Mode, probe: Probe): Promise<Choice | undefined> {
  if (mode === "remote") {
    return (await probe.serverUp()) ? { local: false, remote: true } : undefined;
  }
  const hasIndex = await probe.hasIndex();
  if (mode === "local") {
    if (hasIndex || (await probe.agreeToBuild())) {
      return { local: true, remote: false };
    }
    return undefined;
  }
  const serverUp = await probe.serverUp();
  if (hasIndex || serverUp) {
    return { local: hasIndex, remote: serverUp };
  }
  return (await probe.agreeToBuild()) ? { local: true, remote: false } : undefined;
}
